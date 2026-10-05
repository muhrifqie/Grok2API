"""
Grok2API — OpenAI-compatible API + Admin Panel
Usage: python server.py [--port 8168] [--debug]
"""

import sqlite3, json, uuid, time, asyncio, sys, threading, os, hmac, hashlib, secrets, base64, re, collections
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx, uvicorn
from websockets.asyncio.client import connect as ws_connect

import bulk_login
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse

# ─── Config ─────────────────────────────────────────────
BASE = Path(__file__).parent
DB_PATH = BASE / "grok2api.db"
OLD_JSON = BASE / "accounts.json"
DEBUG = "--debug" in sys.argv
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# The WS gateway takes a chat MODE name in session.model (browser sends "fast"/"auto"/"build"…), not a catalog id.
# Probed 2026-10-04 on a free account (all.har + live): "auto" (answers as Grok 4.5), "fast" and legacy "grok-3"
# stream fine and share the grok-3 quota (30 / 24h). expert / heavy / grok-4 / grok-4-1-thinking / grok-420 / build →
# response.done "incomplete: stream_error" — the browser additionally sends a Castle anti-bot token, which we do NOT forge.
# Public alias → WS mode:
MODELS = {
    "grok-auto": "auto",
    "grok-fast": "fast",
    "grok-3": "grok-3",
    "grok-3-mini": "fast",
    "grok-2": "fast",
    "gpt-4o": "auto",
    "gpt-4": "auto",
}
PROXY_OK = {"grok-3"}  # catalog ids usable through the proxy
# Chat modes as seen in the grok.com picker; `ok` = streams via proxy on a free account; `quota` = /rest/rate-limits model.
MODES = [
    {"mode": "auto", "label": "Auto (Grok 4.5)", "ok": True, "quota": "grok-3", "aliases": ["grok-auto", "gpt-4o", "gpt-4"]},
    {"mode": "fast", "label": "Fast", "ok": True, "quota": "grok-3", "aliases": ["grok-fast", "grok-3-mini", "grok-2"]},
    {"mode": "expert", "label": "Expert", "ok": False, "quota": "grok-4", "aliases": []},
    {"mode": "heavy", "label": "Heavy", "ok": False, "quota": "grok-4", "aliases": []},
    {"mode": "grok-4-1-thinking", "label": "Grok 4.1 Thinking", "ok": False, "quota": "grok-4-1-thinking-1129", "aliases": []},
    {"mode": "grok-420", "label": "Grok 420", "ok": False, "quota": "grok-420", "aliases": []},
    {"mode": "build", "label": "Build (Beta)", "ok": False, "quota": "build", "aliases": []},
]
# Models whose quota we track per account (POST /rest/rate-limits)
QUOTA_MODELS = ["grok-3", "grok-4", "grok-4-1-thinking-1129", "grok-420", "build"]
QUOTA_TTL = 300  # s — cache rate-limit lookups so the admin panel doesn't hammer grok.com


def est_tokens(text: str) -> int:
    # ponytail: chars/4 heuristic — Grok's WS never reports token usage; swap for a real tokenizer if billing needs it
    return (len(text) + 3) // 4

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    migrate_json()
    reload_pool()
    asyncio.create_task(admin_quota())  # warm quota cache → pool can skip exhausted accounts from the start
    print(f"\n  Grok2API ready — {len(pool)} valid accounts")
    print("  Admin: http://localhost:8168/admin")
    print("  API:   http://localhost:8168/v1/chat/completions")
    print("  [warn] admin password = '123' — set env GROK2_ADMIN_PASS to change\n" if ADMIN_PASS == "123" else "")
    log_event("info", "system", f"Server siap · {len(pool)} akun valid di pool")
    yield


app = FastAPI(title="Grok2API", docs_url=None, lifespan=lifespan)

# ─── Admin Auth (HMAC signed-cookie session) ────────────
# ponytail: stateless signed cookie, no session store — survives restart, logout = let it expire or clear cookie

SECRET_FILE = BASE / ".admin_secret"
ADMIN_PASS = os.environ.get("GROK2_ADMIN_PASS", "123")
SESSION_TTL = 7 * 24 * 3600  # 7 days
_EXEMPT = {"/admin/api/login", "/admin/api/logout", "/admin/api/me"}


def _secret() -> bytes:
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip().encode()
    s = secrets.token_hex(32)
    SECRET_FILE.write_text(s)
    return s.encode()


ADMIN_SECRET = _secret()


def make_session() -> str:
    exp = str(int(time.time()) + SESSION_TTL)
    sig = hmac.new(ADMIN_SECRET, exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_session(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, _, sig = token.rpartition(".")
    expected = hmac.new(ADMIN_SECRET, exp.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


@app.middleware("http")
async def admin_guard(request: Request, call_next):
    p = request.url.path
    if p.startswith("/admin/api/") and p not in _EXEMPT:
        if not valid_session(request.cookies.get("admin_session")):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.post("/admin/api/login")
async def admin_login(request: Request):
    body = await request.json()
    if body.get("password", "") != ADMIN_PASS:
        raise HTTPException(401, "Password salah")
    resp = JSONResponse({"ok": True})
    resp.set_cookie("admin_session", make_session(), httponly=True,
                    samesite="lax", max_age=SESSION_TTL, path="/")
    return resp


@app.post("/admin/api/logout")
async def admin_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("admin_session", path="/")
    return resp


@app.get("/admin/api/me")
def admin_me(request: Request):
    return {"authed": valid_session(request.cookies.get("admin_session"))}


# ─── Database ───────────────────────────────────────────

def db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                user_id TEXT DEFAULT '',
                cookie_header TEXT DEFAULT '',
                valid INTEGER DEFAULT 0,
                last_verified TEXT DEFAULT '',
                last_used TEXT DEFAULT '',
                request_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                login_status TEXT DEFAULT 'idle',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                request_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_email TEXT DEFAULT '',
                api_key TEXT DEFAULT '',
                model TEXT DEFAULT '',
                duration_ms INTEGER DEFAULT 0,
                success INTEGER DEFAULT 1,
                error_msg TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE INDEX IF NOT EXISTS idx_acc_valid ON accounts(valid);
            CREATE INDEX IF NOT EXISTS idx_acc_login_status ON accounts(login_status);
            CREATE INDEX IF NOT EXISTS idx_logs_created ON usage_logs(created_at);
        """)
        if c.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0] == 0:
            # Random default key so a fresh deploy isn't guessable (rotate/delete it from the admin panel).
            c.execute("INSERT INTO api_keys (key,name) VALUES (?, 'Default')", (f"sk-grok-{secrets.token_hex(16)}",))
        cols = {r[1] for r in c.execute("PRAGMA table_info(usage_logs)")}
        for col in ("prompt_tokens", "completion_tokens"):
            if col not in cols:
                c.execute(f"ALTER TABLE usage_logs ADD COLUMN {col} INTEGER DEFAULT 0")


SETTINGS_DEFAULTS = {"concurrency": 5, "headless": 1, "login_timeout": 45, "proxies": "",
                     "del_invalid": 1, "del_failed": 1}


def get_setting(key: str):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r is None:
        return SETTINGS_DEFAULTS.get(key)
    try:
        return int(r["value"])
    except (ValueError, TypeError):
        return r["value"]


def set_setting(key: str, value):
    with db() as c:
        c.execute("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))


# ─── Live console (in-memory event ring buffer) ─────────
# Events from any thread (bulk login workers, API handlers) land here; the admin Console page tails them.
CONSOLE = collections.deque(maxlen=3000)
_console_seq = 0
_console_lock = threading.Lock()


def log_event(level: str, source: str, msg: str):
    """level: ok|info|warn|error · source: bulk|login|api|check|system. Thread-safe."""
    global _console_seq
    with _console_lock:
        _console_seq += 1
        ev = {"id": _console_seq, "ts": datetime.now(timezone.utc).isoformat(),
              "level": level, "source": source, "msg": str(msg)[:600]}
        CONSOLE.append(ev)
    if DEBUG:
        print(f"  [{source}/{level}] {msg}")
    return ev


def migrate_json():
    if not OLD_JSON.exists():
        return
    try:
        data = json.loads(OLD_JSON.read_text())
        with db() as c:
            for a in data:
                c.execute(
                    "INSERT OR IGNORE INTO accounts (email,password,user_id,cookie_header,valid,last_verified) VALUES (?,?,?,?,?,?)",
                    (a["email"], a.get("password", ""), a.get("user_id", ""),
                     a.get("cookie_header", ""), 1 if a.get("valid") else 0,
                     a.get("last_verified", "")),
                )
        OLD_JSON.rename(OLD_JSON.with_suffix(".json.bak"))
        print(f"  [db] Migrated {len(data)} accounts from accounts.json")
    except Exception as e:
        print(f"  [db] Migration error: {e}")


# ─── Account Pool ──────────────────────────────────────

pool: list[dict] = []
pool_idx = 0


def reload_pool():
    global pool
    with db() as c:
        rows = c.execute("SELECT * FROM accounts WHERE valid=1 AND cookie_header!=''").fetchall()
        pool = [dict(r) for r in rows]


def next_account():
    """Round-robin, skipping accounts whose cached grok-3 quota is known to be 0 (unknown = try it)."""
    global pool_idx
    if not pool:
        return None
    for _ in range(len(pool)):
        acc = pool[pool_idx % len(pool)]
        pool_idx += 1
        q = QUOTA.get(acc["email"], {}).get("models", {}).get("grok-3")
        if not q or q.get("remaining") != 0:
            return acc
    return None


# ─── Grok quota (rate-limits) ──────────────────────────
# QUOTA[email] = {"at": ts, "models": {model: {remaining,total,window}}, "error": str}
QUOTA: dict[str, dict] = {}


async def fetch_quota(acc: dict, client: httpx.AsyncClient) -> dict:
    headers = {"Cookie": acc["cookie_header"], "User-Agent": UA, "Origin": "https://grok.com",
               "Referer": "https://grok.com/", "Content-Type": "application/json"}
    out = {"at": time.time(), "models": {}, "error": ""}
    try:
        for m in QUOTA_MODELS:
            r = await client.post("https://grok.com/rest/rate-limits", headers=headers,
                                  json={"requestKind": "DEFAULT", "modelName": m})
            if r.status_code == 200 and "remainingQueries" in r.text:
                j = r.json()
                out["models"][m] = {"remaining": j["remainingQueries"], "total": j["totalQueries"],
                                    "window": j["windowSizeSeconds"]}
            elif r.status_code in (401, 403):
                out["error"] = f"HTTP {r.status_code} (cookie kedaluwarsa?)"
                break
        if not out["error"]:
            r = await client.post("https://grok.com/rest/media/imagine/quota_info", headers=headers, json={})
            if r.status_code == 200:
                # {"imagePro": {"available", "remainingQueries", "windowSizeSeconds"}, "video720p": {...}, "image": null, …}
                out["imagine"] = {k: {"remaining": v.get("remainingQueries", 0), "window": v.get("windowSizeSeconds", 0),
                                      "available": v.get("available", False)}
                                  for k, v in r.json().items() if isinstance(v, dict)}
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    QUOTA[acc["email"]] = out
    return out


def _spend_quota(email: str):
    q = QUOTA.get(email, {}).get("models", {}).get("grok-3")
    if q and q["remaining"] > 0:
        q["remaining"] -= 1


# ─── Auth ───────────────────────────────────────────────

def check_key(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    key = auth_header.removeprefix("Bearer ").strip()
    with db() as c:
        row = c.execute("SELECT key FROM api_keys WHERE key=? AND active=1", (key,)).fetchone()
    return row["key"] if row else None


# ─── Grok WebSocket ────────────────────────────────────

def _dbg(msg):
    if DEBUG:
        print(f"  [ws] {msg}")


MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _grok_headers(acc: dict) -> dict:
    return {"Origin": "https://grok.com", "Referer": "https://grok.com/", "User-Agent": UA, "Cookie": acc["cookie_header"]}


async def _load_image(url: str, client: httpx.AsyncClient) -> tuple[bytes, str]:
    """OpenAI image_url (data: URL or http(s)) → (bytes, mime)."""
    if url.startswith("data:"):
        head, _, b64 = url.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        data = base64.b64decode(b64)
    else:
        r = await client.get(url, follow_redirects=True, timeout=20)
        r.raise_for_status()
        data, mime = r.content, r.headers.get("content-type", "image/jpeg").split(";")[0]
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"Gambar terlalu besar ({len(data) // 1024 // 1024} MB, maks 10 MB)")
    return data, mime


async def grok_upload(acc: dict, data: bytes, mime: str, client: httpx.AsyncClient) -> str:
    """POST /http/upload-file-v2/direct (multipart `file`) → fileMetadataId used in file_mention."""
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(mime, "bin")
    r = await client.post("https://grok.com/http/upload-file-v2/direct", headers=_grok_headers(acc),
                          files={"file": (f"upload.{ext}", data, mime)}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Upload gambar gagal: HTTP {r.status_code}")
    return r.json()["fileMetadata"]["fileMetadataId"]


def _flatten(messages: list) -> tuple[str, list[str]]:
    """OpenAI messages → (single prompt text, image urls). Grok gets one turn, so history is inlined."""
    parts, sys_parts, images = [], [], []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            texts = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    texts.append(c.get("text", ""))
                elif c.get("type") == "image_url":
                    u = c.get("image_url")
                    images.append(u.get("url", "") if isinstance(u, dict) else str(u))
            content = " ".join(texts)
        role = m.get("role", "user")
        if role == "system":
            sys_parts.append(content)
        elif role == "assistant":
            parts.append(f"[assistant]: {content}")
        else:
            parts.append(content)
    prompt = "\n\n".join(p for p in parts if p)
    if sys_parts:
        prompt = "[System instructions]\n" + "\n".join(sys_parts) + "\n\n[User message]\n" + prompt
    return prompt, [u for u in images if u]


# Browser-identical session flags (all.har). use_chunk → text arrives as response.chunk events.
_XGROK = {"protocol_capabilities": ["conversation_attached"], "use_chunk": True, "accept_interleaved_phases": True,
          "enable_side_by_side": False, "enable_image_generation": False, "disable_text_follow_ups": True,
          "disable_artifact": True, "force_concise": False, "is_temporary": True, "disable_memory": True}


async def grok_chat(acc: dict, messages: list, model: str):
    """Yields ("content"|"reasoning"|"error", text). Images in messages are uploaded first and attached."""
    prompt, image_urls = _flatten(messages)
    file_ids = []
    if image_urls:
        try:
            async with httpx.AsyncClient() as client:
                for u in image_urls:
                    data, mime = await _load_image(u, client)
                    file_ids.append(await grok_upload(acc, data, mime, client))
            _dbg(f"uploaded {len(file_ids)} image(s)")
        except Exception as e:
            yield ("error", f"{e}")
            return

    def wrap(evt, sid=None):
        msg = {"event": evt}
        if sid:
            msg["session_id"] = sid
        return json.dumps(msg)

    def unwrap(raw):
        msg = json.loads(raw)
        evt = msg.get("event", msg)
        evt["_sid"] = msg.get("session_id", "")
        return evt

    ws_url = f"wss://grok.com/ws/mgw/?uid={acc['user_id']}"
    headers = {**_grok_headers(acc), "Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache", "Pragma": "no-cache"}
    _dbg(f"connecting {ws_url} model={model}")

    try:
        async with ws_connect(ws_url, additional_headers=headers, max_size=10 * 1024 * 1024, open_timeout=15) as ws:
            await ws.send(wrap({"type": "session.create", "event_id": f"evt_init_{uuid.uuid4()}",
                                "session": {"model": model, "x_grok": _XGROK}}))
            session_id = None
            for _ in range(40):
                evt = unwrap(await asyncio.wait_for(ws.recv(), timeout=15))
                t = evt.get("type", "")
                _dbg(f"recv: {t}")
                if t == "error":
                    yield ("error", f"Grok error: {evt.get('error', {}).get('message', evt)}")
                    return
                if t == "session.created":
                    session_id = evt.get("_sid", "")
                if t == "conversation.attached" and session_id:
                    break
            if not session_id:
                yield ("error", "Grok tidak membuat session")
                return

            chunks = [{"mention": {"file_mention": {"file_id": f}}} for f in file_ids] + [{"text": {"text": prompt}}]
            req = {"type": "response.create", "event_id": f"evt_resp_{int(time.time() * 1000)}",
                   "item": {"type": "message", "role": "user",
                            "x_grok": {"client_message_id": str(uuid.uuid4()), "input_chunks": chunks}}}
            if file_ids:
                req["file_attachment_ids"] = file_ids
            await ws.send(wrap(req, sid=session_id))

            while True:
                try:
                    evt = unwrap(await asyncio.wait_for(ws.recv(), timeout=120))
                except asyncio.TimeoutError:
                    yield ("error", "Timeout: Grok tidak merespons 120 detik")
                    return
                t = evt.get("type", "")
                if t == "response.chunk":
                    tx = evt.get("chunk", {}).get("text")
                    if tx and tx.get("text"):
                        # ponytail: only ASSISTANT_RESPONSE seen so far; any other text channel is treated as reasoning
                        ch = tx.get("channel", "CHANNEL_ASSISTANT_RESPONSE")
                        yield ("content" if ch == "CHANNEL_ASSISTANT_RESPONSE" else "reasoning", tx["text"])
                    continue
                _dbg(f"recv: {t}")
                if t in ("response.done", "response.completed"):
                    resp = evt.get("response", {})
                    status = resp.get("status", "completed")
                    if status != "completed":
                        reason = (resp.get("status_details") or {}).get("reason", "")
                        yield ("error", f"Grok response {status}{f': {reason}' if reason else ''}")
                    break
                if t == "error":
                    yield ("error", f"Grok error: {evt.get('error', {}).get('message', evt)}")
                    return

    except Exception as e:
        yield ("error", f"{type(e).__name__}: {e}")


# ─── Imagine (image generation) ────────────────────────
# wss://grok.com/ws/imagine/listen: send reset + prompt item; server streams {"type":"json"} job status and
# {"type":"image","blob":<base64 jpeg>}. Per job: low-res JFIF preview, then final EXIF jpeg whose EXIF embeds the job id.
MEDIA_DIR = BASE / "media"
ASPECTS = {"1:1", "2:3", "3:2", "9:16", "16:9"}
SIZE_TO_ASPECT = {"1024x1024": "1:1", "1024x1536": "2:3", "1536x1024": "3:2", "1024x1792": "9:16", "1792x1024": "16:9"}


async def grok_imagine(acc: dict, prompt: str, n: int = 1, aspect: str = "1:1", pro: bool = False) -> list[dict]:
    headers = {"Origin": "https://grok.com", "User-Agent": UA, "Cookie": acc["cookie_header"]}

    def item(content):
        return json.dumps({"type": "conversation.item.create", "timestamp": int(time.time() * 1000),
                           "item": {"type": "message", "content": [content]}})

    jobs: dict[str, dict] = {}
    finals: list[bytes] = []
    async with ws_connect("wss://grok.com/ws/imagine/listen", additional_headers=headers,
                          max_size=50 * 1024 * 1024, open_timeout=15) as ws:
        await ws.send(item({"type": "reset"}))
        await ws.send(item({"requestId": str(uuid.uuid4()), "text": prompt, "type": "input_text", "properties": {
            "section_count": 0, "is_kids_mode": False, "enable_nsfw": False, "skip_upsampler": False,
            "enable_side_by_side": False, "is_initial": False, "aspect_ratio": aspect, "enable_pro": pro,
            "num_generations": n, "enable_watermark": False}}))
        deadline = time.time() + 180
        while time.time() < deadline:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.time())))
            if msg.get("type") == "image":
                b = base64.b64decode(msg.get("blob", ""))
                if b[6:10] == b"Exif":
                    finals.append(b)
            elif msg.get("type") == "json":
                jid = msg.get("job_id") or msg.get("image_id")
                st = msg.get("current_status")
                if jid:
                    jobs.setdefault(jid, {}).update(id=jid, status=st, width=msg.get("width"), height=msg.get("height"),
                                                    moderated=msg.get("moderated"), mode=msg.get("mode"),
                                                    prompt=msg.get("full_prompt") or prompt)
                if st in ("error", "failed"):
                    raise RuntimeError(f"Imagine gagal: {json.dumps(msg)[:200]}")
                if len(jobs) >= n and all(j["status"] == "completed" for j in jobs.values()):
                    break
            elif msg.get("type") == "error":
                raise RuntimeError(f"Imagine error: {json.dumps(msg)[:200]}")
    MEDIA_DIR.mkdir(exist_ok=True)
    out = []
    for jid, j in jobs.items():
        blob = next((b for b in finals if jid.encode() in b), None)
        if blob is None and finals:
            blob = finals.pop(0)
        if blob is None:
            continue
        (MEDIA_DIR / f"{jid}.jpg").write_bytes(blob)
        out.append({**j, "bytes": blob})
    if not out:
        raise RuntimeError("Imagine selesai tanpa gambar (timeout atau ditolak moderasi)")
    return out


# ─── OpenAI API ─────────────────────────────────────────

def _sse(cid, ts, model, delta, finish=None):
    return f"data: {json.dumps({'id':cid,'object':'chat.completion.chunk','created':ts,'model':model,'choices':[{'index':0,'delta':delta,'finish_reason':finish}]})}\n\n"


MAX_RETRIES = 3  # retry a completion on error-before-output, rotating to another account each time


async def grok_chat_retry(messages, model, meta: dict):
    """Yield ('content'|'reasoning', delta). If Grok errors BEFORE any output (e.g. stream_error / empty),
    rotate to another account and retry up to MAX_RETRIES. meta gets: email, attempts, err ('' on success)."""
    last_err = "Tidak ada akun tersedia"
    for i in range(1, MAX_RETRIES + 1):
        acc = next_account()
        if not acc:
            last_err = "No available account"
            break
        meta["email"], meta["attempts"] = acc["email"], i
        produced, err = False, None
        try:
            async for typ, delta in grok_chat(acc, messages, model):
                if typ == "error":
                    err = delta
                    break
                produced = True
                yield typ, delta
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        if produced:
            meta["err"] = ""  # got output → success (ignore any trailing error, can't un-send)
            return
        last_err = err or "Respon kosong dari Grok"
        log_event("warn", "api", f"retry {i}/{MAX_RETRIES} ({acc['email'].split('@')[0]}): {last_err[:60]}")
    meta["err"] = last_err


async def grok_complete(messages, model, meta: dict):
    """Non-stream: buffer each attempt, retry fully on ANY error (incl. error AFTER partial output —
    safe to discard since nothing is sent to the client yet). Returns (content, reasoning)."""
    last_err = "Tidak ada akun tersedia"
    for i in range(1, MAX_RETRIES + 1):
        acc = next_account()
        if not acc:
            last_err = "No available account"
            break
        meta["email"], meta["attempts"] = acc["email"], i
        content, reasoning, err = "", "", None
        try:
            async for typ, delta in grok_chat(acc, messages, model):
                if typ == "error":
                    err = delta
                    break
                elif typ == "content":
                    content += delta
                elif typ == "reasoning":
                    reasoning += delta
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        if content and not err:
            meta["err"] = ""
            return content, reasoning
        last_err = err or "Respon kosong dari Grok"
        log_event("warn", "api", f"retry {i}/{MAX_RETRIES} ({acc['email'].split('@')[0]}): {last_err[:60]}")
    meta["err"] = last_err
    return "", ""


async def _stream(messages, model, meta: dict):
    """SSE stream with transparent retry. Fills meta = {out, err, email} for usage logging."""
    cid, ts = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time())
    yield _sse(cid, ts, model, {"role": "assistant", "content": ""})
    got = False
    async for typ, delta in grok_chat_retry(messages, model, meta):
        got = True
        if typ == "content":
            meta["out"] += delta
        yield _sse(cid, ts, model, {"content": delta} if typ == "content" else {"reasoning_content": delta})
    if not got and meta.get("err"):
        yield _sse(cid, ts, model, {"content": f"[Error: {meta['err']}]"})
    pt, ct = est_tokens(_prompt_text(messages)), est_tokens(meta["out"])
    yield f"data: {json.dumps({'id':cid,'object':'chat.completion.chunk','created':ts,'model':model,'choices':[{'index':0,'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':pt,'completion_tokens':ct,'total_tokens':pt+ct}})}\n\n"
    yield "data: [DONE]\n\n"


def _prompt_text(messages: list) -> str:
    out = []
    for m in messages:
        c = m.get("content", "")
        out.append(c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if isinstance(p, dict)))
    return "\n".join(out)


@app.get("/v1/models")
async def v1_models():
    return {"object": "list", "data": [{"id": k, "object": "model", "owned_by": "xai"} for k in [*MODELS, "grok-imagine"]]}


@app.post("/v1/chat/completions")
async def v1_completions(request: Request, authorization: str = Header(None)):
    key = check_key(authorization)
    if not key:
        raise HTTPException(401, "Invalid API key")

    body = await request.json()
    messages = body.get("messages", [])
    model = MODELS.get(body.get("model", "grok-auto"), "auto")  # unknown alias → auto (best free mode)
    stream = body.get("stream", False)
    if not pool:
        raise HTTPException(503, "No available account (none valid, or all out of grok-3 quota)")

    t0 = time.time()
    pt = est_tokens(_prompt_text(messages))

    if stream:
        meta = {"out": "", "err": "", "email": ""}

        async def tracked_stream():
            async for chunk in _stream(messages, model, meta):
                yield chunk
            ms = int((time.time() - t0) * 1000)
            _log_usage(meta.get("email") or "?", key, model, ms, not meta["err"], meta["err"], pt, est_tokens(meta["out"]))
        return StreamingResponse(tracked_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    meta = {"out": "", "err": "", "email": ""}
    content, reasoning = await grok_complete(messages, model, meta)
    err = meta.get("err") or ("" if content else "Respon kosong dari Grok")
    ms = int((time.time() - t0) * 1000)
    ct = est_tokens(content)
    _log_usage(meta.get("email") or "?", key, model, ms, not err, err, pt, ct)
    if err:
        raise HTTPException(502, err)

    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}}


def _log_usage(email, key, model, ms, ok, err="", pt=0, ct=0, spend=True):
    if ok and spend:
        _spend_quota(email)
    try:
        with db() as c:
            c.execute("INSERT INTO usage_logs (account_email,api_key,model,duration_ms,success,error_msg,prompt_tokens,completion_tokens) VALUES (?,?,?,?,?,?,?,?)",
                      (email, key, model, ms, 1 if ok else 0, err, pt, ct))
            c.execute("UPDATE accounts SET request_count=request_count+1, last_used=datetime('now') WHERE email=?", (email,))
            if not ok:
                c.execute("UPDATE accounts SET error_count=error_count+1 WHERE email=?", (email,))
            c.execute("UPDATE api_keys SET request_count=request_count+1 WHERE key=?", (key,))
    except Exception:
        pass
    acc = email.split("@")[0] if email else "?"
    if ok:
        log_event("ok", "api", f"{model} · {acc} · {ms}ms · ~{pt + ct} tok")
    else:
        log_event("error", "api", f"{model} · {acc} · GAGAL: {err[:120]}")


@app.post("/v1/images/generations")
async def v1_images(request: Request, authorization: str = Header(None)):
    """OpenAI-compatible image generation via Grok Imagine.
    Body: prompt, n (1-4), size (1024x1024 | 1024x1536 | 1536x1024 | 1024x1792 | 1792x1024) or aspect_ratio,
    quality ("hd" → Imagine Pro, quota 8/24h), response_format ("url" | "b64_json")."""
    key = check_key(authorization)
    if not key:
        raise HTTPException(401, "Invalid API key")
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt wajib diisi")
    n = max(1, min(4, int(body.get("n") or 1)))
    aspect = body.get("aspect_ratio") or SIZE_TO_ASPECT.get(body.get("size", ""), "1:1")
    if aspect not in ASPECTS:
        raise HTTPException(400, f"aspect_ratio harus salah satu dari {sorted(ASPECTS)}")
    pro = body.get("quality") in ("hd", "high") or bool(body.get("pro"))
    fmt = body.get("response_format", "url")
    acc = next_account()
    if not acc:
        raise HTTPException(503, "No available account")

    t0 = time.time()
    model = "imagine-pro" if pro else "imagine"
    try:
        imgs = await grok_imagine(acc, prompt, n, aspect, pro)
    except Exception as e:
        _log_usage(acc["email"], key, model, int((time.time() - t0) * 1000), False, str(e), spend=False)
        raise HTTPException(502, str(e))
    _log_usage(acc["email"], key, model, int((time.time() - t0) * 1000), True, spend=False)

    host = request.headers.get("x-forwarded-host") or request.url.netloc
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    data = []
    for im in imgs:
        d = {"revised_prompt": im.get("prompt"), "x_grok": {k: im.get(k) for k in ("id", "width", "height", "mode", "moderated")}}
        if fmt == "b64_json":
            d["b64_json"] = base64.b64encode(im["bytes"]).decode()
        else:
            d["url"] = f"{scheme}://{host}/v1/media/{im['id']}.jpg"
        data.append(d)
    return {"created": int(time.time()), "data": data}


@app.get("/v1/media/{name}")
async def v1_media(name: str):
    # ponytail: public by unguessable uuid (like signed-URL-less CDNs); add auth if images must stay private
    if not re.fullmatch(r"[0-9a-f-]{36}\.jpg", name) or not (MEDIA_DIR / name).exists():
        raise HTTPException(404)
    return FileResponse(MEDIA_DIR / name, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ─── Admin Panel ────────────────────────────────────────

@app.get("/admin")
async def admin_page():
    return FileResponse(BASE / "admin.html", media_type="text/html")


@app.get("/admin/api/stats")
def admin_stats():
    with db() as c:
        total = c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        valid = c.execute("SELECT COUNT(*) FROM accounts WHERE valid=1").fetchone()[0]
        reqs = c.execute("SELECT COALESCE(SUM(request_count),0) FROM accounts").fetchone()[0]
        # "today" = WIB calendar day (created_at is stored in UTC)
        today, errs, avg_ms, tok = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(success=0),0), COALESCE(AVG(duration_ms),0), "
            "COALESCE(SUM(prompt_tokens+completion_tokens),0) FROM usage_logs "
            "WHERE date(created_at,'+7 hours')=date('now','+7 hours')").fetchone()
        busy = c.execute("SELECT COUNT(*) FROM accounts WHERE login_status IN ('running','queued')").fetchone()[0]
        # Match _ACC_STATUS['failed'] exactly so the dashboard "Login gagal" tile count == the ?f=failed list.
        # Wrong-password accounts (login_status='invalid') are surfaced separately under "Akun invalid".
        failed = c.execute("SELECT COUNT(*) FROM accounts WHERE login_status='failed' OR login_status LIKE 'error%'").fetchone()[0]
    # Capacity = sum of cached grok-3 quota over pool accounts (None until quota fetched at least once)
    qs = [QUOTA[a["email"]]["models"]["grok-3"] for a in pool if "grok-3" in QUOTA.get(a["email"], {}).get("models", {})]
    return {"total": total, "valid": valid, "invalid": total - valid, "failed": failed, "requests": reqs,
            "today": today, "errors_today": errs,
            "ok_today": today - errs, "avg_ms_today": int(avg_ms), "tokens_today": tok, "login_busy": busy,
            "pool_size": len(pool),
            "quota_remaining": sum(q["remaining"] for q in qs) if qs else None,
            "quota_total": sum(q["total"] for q in qs) if qs else None}


@app.get("/admin/api/quota")
async def admin_quota(refresh: int = 0):
    """Per-account Grok quota for QUOTA_MODELS. Cached QUOTA_TTL s; ?refresh=1 forces a re-fetch."""
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT id,email,cookie_header FROM accounts WHERE valid=1 AND cookie_header!=''")]
    stale = [a for a in rows if refresh or time.time() - QUOTA.get(a["email"], {}).get("at", 0) > QUOTA_TTL]
    if stale:
        sem = asyncio.Semaphore(5)
        async with httpx.AsyncClient(timeout=15) as client:
            async def one(a):
                async with sem:
                    await fetch_quota(a, client)
            await asyncio.gather(*(one(a) for a in stale))
    return {"models": QUOTA_MODELS, "proxy_ok": sorted(PROXY_OK), "ttl": QUOTA_TTL, "modes": MODES,
            "accounts": [{"id": a["id"], "email": a["email"], **QUOTA.get(a["email"], {})} for a in rows]}


_MODELS_CACHE: dict = {}


@app.get("/admin/api/models")
async def admin_models(refresh: int = 0):
    """Raw model catalog from grok.com (POST /rest/models) of the first valid account, cached 10 min."""
    if not refresh and time.time() - _MODELS_CACHE.get("at", 0) < 600:
        return _MODELS_CACHE["data"]
    if not pool:
        raise HTTPException(503, "Tidak ada akun valid untuk membaca katalog model")
    acc = pool[0]
    headers = {"Cookie": acc["cookie_header"], "User-Agent": UA, "Origin": "https://grok.com",
               "Referer": "https://grok.com/", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post("https://grok.com/rest/models", headers=headers, json={})
    if r.status_code != 200:
        raise HTTPException(502, f"grok.com /rest/models HTTP {r.status_code}")
    j = r.json()
    data = {
        "source": acc["email"],
        "defaults": {k: v for k, v in j.items() if k.startswith("default")},
        "models": [{
            "id": m.get("modelId", ""), "mode": m.get("modelMode", ""), "badge": (m.get("badgeText") or "").strip(),
            "tags": m.get("tags", []), "backend": m.get("promptingBackend", ""),
            "hidden": "SKIP_LIST_MODES" in m.get("tags", []), "proxy": m.get("modelId") in PROXY_OK,
            "available": True,
        } for m in j.get("models", [])] + [{
            "id": m.get("modelId", ""), "mode": m.get("modelMode", ""), "badge": (m.get("badgeText") or "").strip(),
            "tags": m.get("tags", []), "backend": m.get("promptingBackend", ""), "hidden": False, "proxy": False,
            "available": False,
        } for m in j.get("unavailableModels", [])],
    }
    _MODELS_CACHE.update(at=time.time(), data=data)
    return data


_ACC_SORT = {"id", "email", "valid", "request_count", "error_count", "last_used", "last_verified", "created_at", "login_status"}
_ACC_STATUS = {
    "all": "1=1",
    "valid": "valid=1",
    "invalid": "valid=0",
    "failed": "(login_status='failed' OR login_status LIKE 'error%')",
    "invalid_pw": "login_status='invalid'",
    "busy": "login_status IN ('running','queued')",
    "pending": "(valid=0 OR cookie_header='')",
}
_ACC_COLS = "id,email,user_id,valid,last_verified,last_used,request_count,error_count,login_status,created_at"


@app.get("/admin/api/accounts")
def admin_accounts(page: int = 1, page_size: int = 50, q: str = "", status: str = "all",
                   sort: str = "id", dir: str = "asc"):
    """Server-side paginated. Returns {rows, total, page, page_size, counts, req_sum, err_sum, ever_login}.
    `total` respects q+status; `counts` are unfiltered (for the filter chips)."""
    page = max(1, page)
    page_size = max(1, min(200, page_size))
    sort = sort if sort in _ACC_SORT else "id"
    direction = "DESC" if str(dir).lower() == "desc" else "ASC"
    where = _ACC_STATUS.get(status, "1=1")
    params: list = []
    if q.strip():
        # Escape LIKE wildcards so a literal % or _ in the search doesn't act as a wildcard.
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where += " AND email LIKE ? ESCAPE '\\'"
        params.append(f"%{term}%")
    with db() as c:
        total = c.execute(f"SELECT COUNT(*) FROM accounts WHERE {where}", params).fetchone()[0]
        rows = [dict(r) for r in c.execute(
            f"SELECT {_ACC_COLS} FROM accounts WHERE {where} ORDER BY {sort} {direction} LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size])]
        all_n = c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        valid_n = c.execute("SELECT COUNT(*) FROM accounts WHERE valid=1").fetchone()[0]
        counts = {
            "all": all_n, "valid": valid_n, "invalid": all_n - valid_n,
            "failed": c.execute(f"SELECT COUNT(*) FROM accounts WHERE {_ACC_STATUS['failed']}").fetchone()[0],
            "invalid_pw": c.execute("SELECT COUNT(*) FROM accounts WHERE login_status='invalid'").fetchone()[0],
            "busy": c.execute(f"SELECT COUNT(*) FROM accounts WHERE {_ACC_STATUS['busy']}").fetchone()[0],
        }
        agg = c.execute("SELECT COALESCE(SUM(request_count),0), COALESCE(SUM(error_count),0), "
                        "COALESCE(SUM(CASE WHEN user_id!='' THEN 1 ELSE 0 END),0) FROM accounts").fetchone()
    return {"rows": rows, "total": total, "page": page, "page_size": page_size, "counts": counts,
            "req_sum": agg[0], "err_sum": agg[1], "ever_login": agg[2]}


@app.post("/admin/api/accounts")
async def admin_add_account(request: Request):
    body = await request.json()
    email = body.get("email", "").strip()
    password = body.get("password", "").strip()
    if not email or not password:
        raise HTTPException(400, "Email and password required")
    try:
        with db() as c:
            c.execute("INSERT INTO accounts (email,password) VALUES (?,?)", (email, password))
        return {"ok": True}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Account already exists")


def parse_accounts(text: str) -> tuple[list[tuple[str, str]], int, int]:
    """Parse 'email<sep>password' lines (sep = : | , ; tab space). Dedupe by email (keep first).
    Returns (unique [(email,password)], dup_count, bad_count)."""
    seen, out, dup, bad = set(), [], 0, 0
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        for sep in ["|", ":", ",", ";", "\t", " "]:
            if sep in line:
                email, _, pwd = line.partition(sep)
                break
        else:
            bad += 1
            continue
        email, pwd = email.strip().lower(), pwd.strip()
        if not email or "@" not in email or not pwd:
            bad += 1
            continue
        if email in seen:
            dup += 1
            continue
        seen.add(email)
        out.append((email, pwd))
    return out, dup, bad


@app.post("/admin/api/accounts/bulk")
async def admin_bulk_import(request: Request):
    body = await request.json()
    accounts, dup, bad = parse_accounts(body.get("data", ""))
    added = 0
    with db() as c:
        for email, pwd in accounts:
            cur = c.execute("INSERT OR IGNORE INTO accounts (email,password) VALUES (?,?)", (email, pwd))
            added += cur.rowcount
    exists = len(accounts) - added
    return {"added": added, "duplicate_in_file": dup, "already_in_db": exists, "invalid_lines": bad,
            "total_unique": len(accounts)}


@app.delete("/admin/api/accounts/{aid}")
def admin_del_account(aid: int):
    with db() as c:
        c.execute("DELETE FROM accounts WHERE id=?", (aid,))
    reload_pool()
    return {"ok": True}


@app.post("/admin/api/accounts/{aid}/check")
def admin_check_account(aid: int):
    with db() as c:
        row = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
    if not row:
        raise HTTPException(404)
    cookie = row["cookie_header"]
    valid = False
    if cookie:
        try:
            r = httpx.get("https://grok.com/api/auth/session", headers={"Cookie": cookie, "User-Agent": UA},
                          follow_redirects=True, timeout=10)
            valid = r.status_code == 200 and r.json().get("status") == "authenticated"
        except Exception:
            pass
    now = datetime.now(timezone.utc).isoformat()
    with db() as c:
        c.execute("UPDATE accounts SET valid=?, last_verified=? WHERE id=?", (1 if valid else 0, now, aid))
    reload_pool()
    log_event("ok" if valid else "warn", "check", f"{row['email'].split('@')[0]} · cookie {'valid' if valid else 'invalid'}")
    return {"valid": valid}


@app.post("/admin/api/accounts/{aid}/login")
def admin_login_account(aid: int):
    with db() as c:
        row = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
    if not row:
        raise HTTPException(404)
    if row["login_status"] == "running":
        return {"status": "already_running"}
    with db() as c:
        c.execute("UPDATE accounts SET login_status='running' WHERE id=?", (aid,))

    acc = {"id": aid, "email": row["email"], "password": row["password"], "cookie_header": row["cookie_header"]}
    log_event("info", "login", f"▶ Login manual: {row['email'].split('@')[0]}")

    def _do():
        from login_camoufox import login_account
        headless = bool(get_setting("headless"))
        res = login_account(row["email"], row["password"], headless=headless, timeout=int(get_setting("login_timeout")))
        _save_login(acc, res)  # persists + emits event + refreshes pool
        reload_pool()

    threading.Thread(target=_do, daemon=True).start()
    return {"status": "started"}


@app.post("/admin/api/accounts/check-all")
def admin_check_all():
    with db() as c:
        rows = c.execute("SELECT id,cookie_header FROM accounts WHERE cookie_header!=''").fetchall()
    results = []
    for row in rows:
        valid = False
        try:
            r = httpx.get("https://grok.com/api/auth/session", headers={"Cookie": row["cookie_header"], "User-Agent": UA},
                          follow_redirects=True, timeout=10)
            valid = r.status_code == 200 and r.json().get("status") == "authenticated"
        except Exception:
            pass
        now = datetime.now(timezone.utc).isoformat()
        with db() as c:
            c.execute("UPDATE accounts SET valid=?,last_verified=? WHERE id=?", (1 if valid else 0, now, row["id"]))
        results.append({"id": row["id"], "valid": valid})
    reload_pool()
    return results


@app.post("/admin/api/accounts/login-all")
def admin_login_all():
    with db() as c:
        rows = c.execute("SELECT * FROM accounts WHERE (valid=0 OR cookie_header='') AND login_status!='running'").fetchall()
    if not rows:
        return {"queued": 0}
    ids = [r["id"] for r in rows]
    with db() as c:
        for aid in ids:
            c.execute("UPDATE accounts SET login_status='queued' WHERE id=?", (aid,))

    def _batch():
        from login_camoufox import login_browser
        for row in rows:
            aid = row["id"]
            with db() as c:
                c.execute("UPDATE accounts SET login_status='running' WHERE id=?", (aid,))
            try:
                result = login_browser(row["email"], row["password"], headless=True)
                with db() as c:
                    if result:
                        now = datetime.now(timezone.utc).isoformat()
                        c.execute("UPDATE accounts SET user_id=?,cookie_header=?,valid=1,last_verified=?,login_status='success' WHERE id=?",
                                  (result["user_id"], result["cookie_header"], now, aid))
                    else:
                        c.execute("UPDATE accounts SET login_status='failed' WHERE id=?", (aid,))
            except Exception as e:
                with db() as c:
                    c.execute("UPDATE accounts SET login_status=? WHERE id=?", (f"error: {str(e)[:80]}", aid))
            reload_pool()

    threading.Thread(target=_batch, daemon=True).start()
    return {"queued": len(ids)}


# ─── Bulk login (concurrent) ───────────────────────────
# Uses bulk_login.run_bulk with a thread pool. Wrong-password accounts come back "invalid" and are skipped.

_login_db_lock = threading.Lock()
_last_pool_reload = 0.0


def _save_login(acc: dict, res: dict, prune: bool = False):
    """Persist one bulk-login result (called from worker threads → serialize writes).
    prune=True (bulk only): auto-delete wrong-password / failed accounts per settings del_invalid / del_failed."""
    global _last_pool_reload
    now = datetime.now(timezone.utc).isoformat()
    status = res.get("status", "error")
    # Auto-prune policy (bulk only). NB: 'failed' = timeout/error, which is often retryable — toggle in settings.
    del_inv = prune and status == "invalid" and get_setting("del_invalid")
    del_fail = prune and status in ("timeout", "error") and get_setting("del_failed")
    deleted = del_inv or del_fail
    with _login_db_lock, db() as c:
        if status == "success":
            c.execute("UPDATE accounts SET user_id=?,cookie_header=?,valid=1,last_verified=?,login_status='success' WHERE id=?",
                      (res["user_id"], res["cookie_header"], now, acc["id"]))
        elif status == "skipped":
            c.execute("UPDATE accounts SET valid=1,last_verified=?,login_status='success' WHERE id=?", (now, acc["id"]))
        elif deleted:
            c.execute("DELETE FROM accounts WHERE id=?", (acc["id"],))
        elif status == "invalid":
            c.execute("UPDATE accounts SET valid=0,login_status='invalid' WHERE id=?", (acc["id"],))
        elif status == "timeout":
            c.execute("UPDATE accounts SET login_status='failed' WHERE id=?", (acc["id"],))
        else:
            c.execute("UPDATE accounts SET login_status=? WHERE id=?", (f"error: {res.get('detail','')[:60]}", acc["id"]))
    # Throttled pool refresh so freshly logged-in accounts start serving during a long run.
    if status in ("success", "skipped") and time.time() - _last_pool_reload > 8:
        _last_pool_reload = time.time()
        reload_pool()
    name = acc["email"].split("@")[0]
    if deleted:
        log_event("warn", "bulk", f"{name} · {'password salah' if status == 'invalid' else 'gagal'} → dihapus dari DB")
    else:
        lvl = {"success": "ok", "skipped": "info", "invalid": "warn"}.get(status, "error")
        label = {"success": "login OK", "skipped": "cookie masih valid", "invalid": "password salah"}.get(status, "gagal")
        log_event(lvl, "bulk", f"{name} · {label}{(' — ' + res.get('detail', '')) if status not in ('success', 'skipped') else ''}")


_BULK_SCOPES = {
    "pending": "valid=0 OR cookie_header=''",           # never logged in / currently invalid
    "failed": "login_status='failed' OR login_status LIKE 'error%'",
    "invalid": "login_status='invalid'",               # wrong password — retry only if you fixed it
    "all": "1=1",
}


@app.post("/admin/api/accounts/bulk-login")
async def admin_bulk_login(request: Request):
    if bulk_login.status()["running"]:
        return {"status": "already_running"}
    body = await request.json()
    scope = body.get("scope", "pending")
    where = _BULK_SCOPES.get(scope, _BULK_SCOPES["pending"])
    limit = int(body.get("limit") or 0)
    conc = int(body.get("concurrency") or get_setting("concurrency"))
    conc = max(1, min(40, conc))
    headless = bool(body.get("headless", get_setting("headless")))
    timeout = int(body.get("login_timeout") or get_setting("login_timeout"))
    proxies = [p.strip() for p in str(get_setting("proxies") or "").splitlines() if p.strip()]
    with db() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT id,email,password,cookie_header FROM accounts WHERE ({where}) AND login_status!='running' ORDER BY id")]
    if limit > 0:
        rows = rows[:limit]
    if not rows:
        return {"queued": 0}
    with _login_db_lock, db() as c:
        c.executemany("UPDATE accounts SET login_status='queued' WHERE id=?", [(r["id"],) for r in rows])

    log_event("info", "bulk", f"▶ Login massal dimulai: {len(rows)} akun · {conc} worker · {len(proxies) or 'tanpa'} proxy · scope={scope}")

    def runner():
        bulk_login.run_bulk(rows, conc, headless, lambda a, r: _save_login(a, r, prune=True),
                            check_existing=True, login_timeout=timeout, proxies=proxies)
        s = bulk_login.status()
        log_event("info", "bulk", f"■ Selesai: {s['success']} berhasil · {s['skipped']} dilewati · {s['invalid']} pw salah · {s['failed']} gagal")
        reload_pool()

    threading.Thread(target=runner, daemon=True).start()
    return {"queued": len(rows), "concurrency": conc, "headless": headless, "proxies": len(proxies)}


@app.get("/admin/api/accounts/bulk-login/status")
def admin_bulk_login_status():
    return bulk_login.status()


@app.post("/admin/api/accounts/bulk-login/stop")
def admin_bulk_login_stop():
    bulk_login.request_stop()
    return {"ok": True}


@app.get("/admin/api/settings")
def admin_get_settings():
    return {k: get_setting(k) for k in SETTINGS_DEFAULTS}


@app.post("/admin/api/settings")
async def admin_set_settings(request: Request):
    body = await request.json()
    for k in SETTINGS_DEFAULTS:
        if k in body:
            set_setting(k, int(body[k]) if str(body[k]).lstrip("-").isdigit() else body[k])
    return {k: get_setting(k) for k in SETTINGS_DEFAULTS}


@app.get("/admin/api/console")
def admin_console(after: int = 0, limit: int = 800):
    """Tail live events after `after` (event id). Returns at most `limit` newest new events + current last_id."""
    with _console_lock:
        evs = [e for e in CONSOLE if e["id"] > after][-limit:]
        seq = _console_seq
        dropped = len(CONSOLE) and CONSOLE[0]["id"] - 1 > after > 0  # buffer wrapped past the caller's cursor
    return {"events": evs, "last_id": seq, "gap": bool(dropped)}


# ─── API Keys ──────────────────────────────────────────

@app.get("/admin/api/keys")
def admin_keys():
    with db() as c:
        rows = c.execute("SELECT * FROM api_keys ORDER BY id").fetchall()
    return [dict(r) for r in rows]


@app.post("/admin/api/keys")
async def admin_create_key(request: Request):
    body = await request.json()
    name = body.get("name", "Unnamed")
    key = f"grok-{uuid.uuid4().hex[:24]}"
    with db() as c:
        c.execute("INSERT INTO api_keys (key,name) VALUES (?,?)", (key, name))
    return {"key": key, "name": name}


@app.delete("/admin/api/keys/{kid}")
def admin_del_key(kid: int):
    with db() as c:
        c.execute("DELETE FROM api_keys WHERE id=?", (kid,))
    return {"ok": True}


# ─── Logs ──────────────────────────────────────────────

@app.get("/admin/api/logs")
def admin_logs():
    with db() as c:
        rows = c.execute("SELECT * FROM usage_logs ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]


# ─── Startup ───────────────────────────────────────────

if __name__ == "__main__":
    port, host = 8168, os.environ.get("GROK2_HOST", "0.0.0.0")
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
        if a == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]
    uvicorn.run(app, host=host, port=port)

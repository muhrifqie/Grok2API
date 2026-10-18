"""
Grok2API — OpenAI-compatible API + Admin Panel
Usage: python server.py [--port 8168] [--debug]
"""

import sqlite3, json, uuid, time, asyncio, sys, threading, os, hmac, hashlib, secrets, base64, re, collections, subprocess, random
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx, uvicorn
from websockets.asyncio.client import connect as ws_connect

import bulk_login
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, RedirectResponse, HTMLResponse

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
    asyncio.create_task(autoheal_loop())  # periodic cookie health sweep (setting: autoheal)
    threading.Thread(target=_jio_auto_keepalive_loop, daemon=True).start()  # auto keep Jio sessions alive
    threading.Thread(target=_jio_watch_loop, daemon=True).start()  # arsip link + lapor sesi mati ke Telegram
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
            -- Jio wrapped-link claims: 1 row per Jio session id (claim = pembeli buka link bungkus /v1/jio/go/).
            CREATE TABLE IF NOT EXISTS jio_claims (jio_id INTEGER PRIMARY KEY, claims INTEGER DEFAULT 0, first_at TEXT, last_at TEXT);
            -- Arsip link terakhir per sesi — TAHAN LAMA walau sesi di data.db mati/hilang (jadi link tak pernah lenyap).
            CREATE TABLE IF NOT EXISTS jio_links (jio_id INTEGER PRIMARY KEY, phone TEXT, link TEXT, link_at TEXT,
                                                  last_status TEXT, died_at TEXT, updated_at TEXT);
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
        if "fail_count" not in {r[1] for r in c.execute("PRAGMA table_info(accounts)")}:
            c.execute("ALTER TABLE accounts ADD COLUMN fail_count INTEGER DEFAULT 0")


SETTINGS_DEFAULTS = {"concurrency": 8, "headless": 1, "login_timeout": 45, "proxies": "",
                     "del_invalid": 1, "del_failed": 1,
                     "autoheal": 1, "autoheal_interval_min": 60, "autoheal_relogin": 0,
                     "ctx_budget": 32000,  # est. tokens of chat history sent to Grok (latest turn always whole)
                     "cache_ttl": 600,     # seconds an identical completion is served from cache (0 = off)
                     "smart_prompt": 1,    # prepend QUALITY_PROMPT to every chat (per request: "smart_prompt": false)
                     "jio_key": "",        # key for the public GET /v1/jio/link (empty = public endpoint disabled)
                     "jio_provider": "grizzly", "grizzly_key": "", "smsbower_key": "",  # Jio generator (full.js)
                     "jio_root": "", "jio_db": "",  # path to the jio-google-ai toolkit + its data.db (env JIO_ROOT/JIO_DB override)
                     "jio_use_proxy": 0,  # proxy saat GENERATE (beli nomor/login). 1 = proxies.txt (IP India), 0 = IP VPS
                     "jio_ka_proxy": 0,   # proxy saat KEEP-ALIVE/refresh. 0 = IP VPS (stabil, disarankan — sesi tak mati)
                     "jio_tg_token": "", "jio_tg_chat": ""}  # Telegram bot token + chat id untuk laporan sesi mati


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
_uses: collections.Counter = collections.Counter()  # per-email pick count → round-robin tie-break


def reload_pool():
    global pool
    with db() as c:
        rows = c.execute("SELECT * FROM accounts WHERE valid=1 AND cookie_header!=''").fetchall()
        pool = [dict(r) for r in rows]


def _grok3_remaining(email: str) -> int:
    """Cached grok-3 quota left. Unknown (not yet fetched) = treat as full so fresh accounts get used."""
    q = QUOTA.get(email, {}).get("models", {}).get("grok-3")
    return 9999 if not q else q.get("remaining", 9999)


def next_account():
    """Pick the account with the MOST remaining grok-3 quota; ties (incl. all-unknown) broken by
    fewest picks so load spreads. _spend_quota decrements the cache each use → self-balancing. Skips known-0."""
    if not pool:
        return None
    best_rem = max(_grok3_remaining(a["email"]) for a in pool)
    if best_rem <= 0:
        return None  # every account's quota is known-exhausted
    top = [a for a in pool if _grok3_remaining(a["email"]) == best_rem]
    acc = min(top, key=lambda a: _uses[a["email"]])
    _uses[acc["email"]] += 1
    return acc


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
            elif r.status_code == 403 and ("Just a moment" in r.text or "cf-" in r.text or "challenge" in r.text.lower()):
                # Cloudflare bot challenge (common from datacenter IPs) — NOT an expired cookie; the WS chat still works.
                out["error"] = "cloudflare (IP diblokir CF — cookie kemungkinan masih OK)"
                break
            elif r.status_code in (401, 403):
                out["error"] = f"cookie kedaluwarsa (HTTP {r.status_code})"
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


def _session_state(cookie: str) -> bool | None:
    """Cookie liveness via REST: True=authenticated, False=expired (401/unauth), None=can't tell
    (Cloudflare challenge from a datacenter IP, or network error) → caller must NOT demote on None."""
    try:
        r = httpx.get("https://grok.com/api/auth/session", headers={"Cookie": cookie, "User-Agent": UA},
                      follow_redirects=True, timeout=10)
        if r.status_code == 200:
            return r.json().get("status") == "authenticated"
        if r.status_code == 403 and ("Just a moment" in r.text or "cf-" in r.text):
            return None
        return False
    except Exception:
        return None


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


CONCISE_RULE = ("Be brief: no filler, pleasantries or restating the question; keep only what the user needs. "
                "Keep code, commands, numbers and technical facts complete. For calculations or logic keep the "
                "working minimal but still verify it before the final answer. Reply in the user's language.")

# Default quality rules (setting smart_prompt=1, opt out per request with "smart_prompt": false).
# Tuned on eval_grok.py failures: Grok tends to put an unverified number in a bold headline and only then
# reason (getting it right below), claims to have "checked sources", and miscounts letters/weekdays.
QUALITY_PROMPT = (
    "You are a sharp, careful assistant. Rules:\n"
    "1. Reply in the user's language (Indonesian if unclear), natural and clear.\n"
    "2. Math, counting, dates, logic or code: reason step by step BEFORE stating any result, double-check it, then give "
    "the final answer last. Never put an unverified number in a heading. Count letters by spelling the word out. "
    "Weekday/date arithmetic: N days after day D = D + (N mod 7), the start day is not counted.\n"
    "Self-contained reasoning questions (math, logic, puzzles) need no web search: answer from the given facts.\n"
    "3. Never invent facts, numbers, prices, quotes, sources or people's opinions, and never claim you checked a source "
    "unless you actually did. If unsure, or it is real-time data you cannot verify, say so briefly and tell the user how to check.\n"
    "4. If the answer hinges on details the user has not given (origin, weight, budget, account, version), ask for "
    "them in one short message instead of guessing numbers; for other gaps, answer with the assumption stated.\n"
    "5. Follow format and length constraints exactly (e.g. 'JSON only', max characters, number of items).\n"
    "6. Match depth to the question: short for simple questions; headings/lists/tables only when they help."
)

REVIEW_PROMPT = (
    "You are a meticulous reviewer. You get a user's question (with conversation context) and a draft answer. "
    "Silently check the draft for wrong arithmetic, logic, counting or dates, invented facts/sources/quotes, missed parts "
    "of the question, and violated format or length constraints. Then output ONLY the final corrected answer for the user, "
    "in the language and format the user asked for. If the draft is already correct, output it (you may tighten it). "
    "Never mention the draft or the review."
)
OLD_TURN_MAX = 2000  # est. tokens kept per older history turn (the latest turn is never clipped)


def _compact(text: str) -> str:
    """Lossless-ish shrink: trailing spaces, 3+ blank lines, minify a message that is pure JSON.
    Indentation is untouched (code stays valid)."""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    s = text.strip()
    if len(s) > 200 and s[:1] in "[{":
        try:
            return json.dumps(json.loads(s), ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            pass
    return text


def _clip(text: str, max_tok: int) -> str:
    """Keep head + tail of an over-long older turn."""
    if est_tokens(text) <= max_tok:
        return text
    keep = max_tok * 2  # chars per side (~4 chars/token, half each side)
    return f"{text[:keep]}\n[... {est_tokens(text) - max_tok} tokens truncated ...]\n{text[-keep:]}"


def _flatten(messages: list, concise: bool = False, budget: int | None = None, smart: bool = False) -> tuple[str, list[str]]:
    """OpenAI messages → (single prompt text, image urls). Grok gets one turn, so history is inlined.
    Saves context: only the LATEST user message's images are uploaded (older ones → placeholder),
    older turns are clipped and the oldest dropped once the history exceeds `budget` est. tokens."""
    budget = budget or int(get_setting("ctx_budget") or 32000)
    last_user = max((i for i, m in enumerate(messages) if m.get("role", "user") not in ("system", "assistant")), default=-1)
    sys_parts, turns, images = [], [], []
    for i, m in enumerate(messages):
        content = m.get("content", "")
        if isinstance(content, list):
            texts = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    texts.append(c.get("text", ""))
                elif c.get("type") == "image_url":
                    if i == last_user:
                        u = c.get("image_url")
                        images.append(u.get("url", "") if isinstance(u, dict) else str(u))
                    else:
                        texts.append("[earlier image omitted]")
            content = " ".join(texts)
        content = _compact(content or "")
        role = m.get("role", "user")
        if role == "system":
            sys_parts.append(content)
        elif content:
            turns.append(("Assistant" if role == "assistant" else "User", content))
    if smart:
        sys_parts.insert(0, QUALITY_PROMPT)  # first, so the client's own system prompt can still override it
    if concise:
        sys_parts.append(CONCISE_RULE)

    # Newest → oldest: latest turn always kept whole; older turns clipped, dropped once over budget.
    used, kept, dropped = est_tokens("\n".join(sys_parts)), [], 0
    for n, (who, text) in enumerate(reversed(turns)):
        if n:
            text = _clip(text, OLD_TURN_MAX)
            if used + est_tokens(text) > budget:
                dropped = len(turns) - n
                break
        kept.append((who, text))
        used += est_tokens(text)
    kept.reverse()

    # Grok gets ONE message, so label every turn and fence off the current one — otherwise an
    # earlier instruction ("pick a number 1-100") gets answered instead of the latest question.
    current = kept.pop()[1] if kept else ""
    history = "\n\n".join(f"{who}: {text}" for who, text in kept)
    if dropped:
        history = f"[... {dropped} earlier messages trimmed to save context ...]" + (f"\n\n{history}" if history else "")
    blocks = []
    if sys_parts:
        blocks.append("[System instructions]\n" + "\n".join(sys_parts))
    if history:
        blocks.append("[Conversation so far — context only]\n" + history)
    prompt = "\n\n".join(blocks + ["[Current user message — reply to this]\n" + current]) if blocks else current
    return prompt, [u for u in images if u]


# Browser-identical session flags (all.har). use_chunk → text arrives as response.chunk events.
_XGROK = {"protocol_capabilities": ["conversation_attached"], "use_chunk": True, "accept_interleaved_phases": True,
          "enable_side_by_side": False, "enable_image_generation": False, "disable_text_follow_ups": True,
          "disable_artifact": True, "force_concise": False, "is_temporary": True, "disable_memory": True}


async def grok_chat(acc: dict, messages: list, model: str, style: dict | None = None):
    """Yields ("content"|"reasoning"|"error", text). Images in messages are uploaded first and attached.
    style = {"concise": bool, "smart": bool} → _flatten prompt rules; concise also sets Grok's native force_concise."""
    style = style or {}
    concise = bool(style.get("concise"))
    prompt, image_urls = _flatten(messages, concise, smart=bool(style.get("smart")))
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
                                "session": {"model": model, "x_grok": {**_XGROK, "force_concise": concise}}}))
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


# ─── Imagine video ─────────────────────────────────────
# REST POST /rest/app-chat/conversations/new {modelName:"imagine-video-gen", mediaGenInput.textToVideo} streams NDJSON;
# result.response.streamingVideoGenerationResponse reports progress, the progress=100 chunk carries videoUrl
# (relative to assets.grok.com). Quota: video720p = 1 per account per 24h (/rest/media/imagine/quota_info).
# This VPS's IP gets a Cloudflare challenge on REST → GROK_EGRESS_PROXY (socks5h tunnel, pm2 grok-egress).
EGRESS_PROXY = os.environ.get("GROK_EGRESS_PROXY") or None
VIDEO_MODEL = "imagine-video"
VIDEO_TRIES = 12  # decoy/429 replies are fast (~5s), real job ~35s
VIDEO_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
_vid_block: dict[str, float] = {}  # email → ts until its video quota is back


class VideoQuotaError(RuntimeError):
    pass


class DecoyVideo(VideoQuotaError):
    """Some accounts get a canned stock clip (lighthouse, farmers market…) instead of the prompt: done in ~5s,
    progress 20/45/70/90/100, final chunk without rRated/moderated. Real jobs take ~35s and carry rRated."""


def next_video_account():
    """Least-used account that has NOT made a video in the last 24h (usage_logs survives restarts)
    and isn't blocked by a usage-limit reply."""
    with db() as c:
        used = {r[0] for r in c.execute("SELECT DISTINCT account_email FROM usage_logs WHERE model=? "
                                        "AND (success=1 OR error_msg='decoy') AND created_at > datetime('now','-1 day')",
                                        (VIDEO_MODEL,))}
    now = time.time()
    cands = [a for a in pool if a["email"] not in used and _vid_block.get(a["email"], 0) < now]
    if not cands:
        return None
    # random, not least-used: decoy accounts cluster in pool order (old bulk batches), a sequential walk hits only those
    acc = random.choice(cands)
    _uses[acc["email"]] += 1
    return acc


async def grok_video(acc: dict, prompt: str, aspect: str = "9:16", duration: int = 6) -> dict:
    # Old UA / --mode=normal got canned stock clips (lighthouse…) back in ~5s instead of the prompt → keep both as-is.
    headers = {**_grok_headers(acc), "User-Agent": VIDEO_UA, "Referer": "https://grok.com/imagine", "Content-Type": "application/json"}
    body = {"modelName": "imagine-video-gen", "message": f"{prompt} --mode=custom", "temporary": True,
            "mediaGenInput": {"textToVideo": {"prompt": prompt, "aspectRatio": aspect, "duration": duration,
                                              "resolutionName": "720p"}},
            "sendFinalMetadata": True, "enableSideBySide": False, "kind": "CONVERSATION_KIND_IMAGINE",
            "enableImageStreaming": True, "responseMetadata": {"experiments": [], "modelConfigOverride": {"modelMap": {}}}}
    final, moderated, err = None, False, ""
    async with httpx.AsyncClient(proxy=EGRESS_PROXY, timeout=httpx.Timeout(30, read=240)) as client:
        async with client.stream("POST", "https://grok.com/rest/app-chat/conversations/new", headers=headers, json=body) as r:
            if r.status_code == 429:
                raise VideoQuotaError("rate limited (429)")
            if r.status_code != 200:
                txt = (await r.aread()).decode(errors="replace")
                raise RuntimeError("Cloudflare (egress proxy mati?)" if "Just a moment" in txt else f"HTTP {r.status_code}: {txt[:150]}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if j.get("error"):
                    err = json.dumps(j["error"])[:200]
                    continue
                resp = (j.get("result") or {}).get("response") or {}
                v = resp.get("streamingVideoGenerationResponse")
                if v:
                    moderated = moderated or bool(v.get("moderated"))
                    if v.get("progress") == 100 and v.get("videoUrl"):
                        final = v
                se = resp.get("streamError") or {}
                if se:
                    err = se.get("message") or json.dumps(se)[:200]
        if not final:
            low = err.lower()
            if any(k in low for k in ("usage", "limit", "quota", "too many")):
                raise VideoQuotaError(err)
            raise RuntimeError("Video ditolak moderasi" if moderated else f"Video gagal: {err or 'tanpa hasil'}")
        if "rRated" not in final:
            raise DecoyVideo("akun dapat klip stok, bukan prompt")
        dl = await client.get(f"https://assets.grok.com/{final['videoUrl']}", headers=_grok_headers(acc), timeout=120)
        if dl.status_code != 200 or not dl.content:
            raise RuntimeError(f"Download video gagal: HTTP {dl.status_code}")
    vid = final.get("videoId") or str(uuid.uuid4())
    MEDIA_DIR.mkdir(exist_ok=True)
    (MEDIA_DIR / f"{vid}.mp4").write_bytes(dl.content)
    return {"id": vid, "bytes": dl.content, "prompt": final.get("videoPrompt") or prompt, "width": final.get("width"),
            "height": final.get("height"), "resolution": final.get("resolutionName"), "moderated": moderated}


# ─── OpenAI API ─────────────────────────────────────────

def _sse(cid, ts, model, delta, finish=None):
    return f"data: {json.dumps({'id':cid,'object':'chat.completion.chunk','created':ts,'model':model,'choices':[{'index':0,'delta':delta,'finish_reason':finish}]})}\n\n"


MAX_RETRIES = 3  # retry a completion on error-before-output, rotating to another account each time


def _fallback_model(mode: str) -> str:
    """Alternate Grok MODE for retries: auto<->fast (grok-3 → fast). A dead mode on one account may work on the other.
    NB: receives the resolved mode name (what session.create expects), not the API alias."""
    return "fast" if mode != "fast" else "auto"


def _attempt_model(model: str, i: int) -> str:
    """Attempt 1 = requested model, then alternate to the fallback and back."""
    return model if i % 2 == 1 else _fallback_model(model)


async def grok_chat_retry(messages, model, meta: dict, style: dict | None = None):
    """Yield ('content'|'reasoning', delta). If Grok errors BEFORE any output (e.g. stream_error / empty),
    rotate to another account and retry up to MAX_RETRIES. meta gets: email, attempts, err ('' on success)."""
    last_err = "Tidak ada akun tersedia"
    for i in range(1, MAX_RETRIES + 1):
        acc = next_account()
        if not acc:
            last_err = "No available account"
            break
        use_model = _attempt_model(model, i)
        meta["email"], meta["attempts"], meta["model_used"] = acc["email"], i, use_model
        produced, err = False, None
        try:
            async for typ, delta in grok_chat(acc, messages, use_model, style):
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
        log_event("warn", "api", f"retry {i}/{MAX_RETRIES} ({acc['email'].split('@')[0]} · {use_model}): {last_err[:60]}")
    meta["err"] = last_err


async def grok_complete(messages, model, meta: dict, style: dict | None = None):
    """Non-stream: buffer each attempt, retry fully on ANY error (incl. error AFTER partial output —
    safe to discard since nothing is sent to the client yet). Returns (content, reasoning)."""
    last_err = "Tidak ada akun tersedia"
    for i in range(1, MAX_RETRIES + 1):
        acc = next_account()
        if not acc:
            last_err = "No available account"
            break
        use_model = _attempt_model(model, i)
        meta["email"], meta["attempts"], meta["model_used"] = acc["email"], i, use_model
        content, reasoning, err = "", "", None
        try:
            async for typ, delta in grok_chat(acc, messages, use_model, style):
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
        log_event("warn", "api", f"retry {i}/{MAX_RETRIES} ({acc['email'].split('@')[0]} · {use_model}): {last_err[:60]}")
    meta["err"] = last_err
    return "", ""


async def _stream(messages, model, meta: dict, style: dict | None = None):
    """SSE stream with transparent retry. Fills meta = {out, reasoning, err, email} for logging + cache."""
    cid, ts = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time())
    yield _sse(cid, ts, model, {"role": "assistant", "content": ""})
    got = False
    async for typ, delta in grok_chat_retry(messages, model, meta, style):
        got = True
        if typ == "content":
            meta["out"] += delta
        else:
            meta["reasoning"] = meta.get("reasoning", "") + delta
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


LITE_MODELS = ["grok-auto-lite", "grok-fast-lite"]  # "-lite" suffix = concise mode on any alias
PRO_MODELS = ["grok-auto-pro", "grok-fast-pro"]     # "-pro" suffix = draft + self-review (2 Grok calls)


def _resolve_model(name: str) -> tuple[str, bool, bool]:
    """API alias → (Grok mode, concise, pro). Unknown alias → auto (best free mode)."""
    lite, pro = name.endswith("-lite"), name.endswith("-pro")
    base = name.rsplit("-", 1)[0] if (lite or pro) else name
    return MODELS.get(base, "auto"), lite, pro


def _has_images(messages: list) -> bool:
    last = messages[-1] if messages else {}
    return isinstance(last.get("content"), list) and any(isinstance(c, dict) and c.get("type") == "image_url" for c in last["content"])


def _review_messages(messages: list, draft: str, concise: bool) -> list:
    """Second pass for -pro: the same question (as context text) + the draft → corrected final answer."""
    question, _ = _flatten(messages, concise)
    return [{"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": f"[Question with context]\n{question}\n\n[Draft answer]\n{draft}"}]


# ─── Response cache (identical request → no Grok call, no quota spent) ───
# ponytail: in-memory LRU, lost on restart and per-process. Upgrade path: SQLite/Redis if multi-worker.
_CACHE: "collections.OrderedDict[str, tuple[float, str, str]]" = collections.OrderedDict()
CACHE_MAX = 500


def _cache_key(model: str, style, messages: list) -> str:
    return hashlib.sha256(json.dumps([model, style, messages], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _cache_get(k: str):
    ttl = int(get_setting("cache_ttl") or 0)
    hit = _CACHE.get(k)
    if not hit or ttl <= 0 or time.time() - hit[0] > ttl:
        return None
    _CACHE.move_to_end(k)
    return hit[1], hit[2]


def _cache_put(k: str, content: str, reasoning: str):
    if not content or int(get_setting("cache_ttl") or 0) <= 0:
        return
    _CACHE[k] = (time.time(), content, reasoning)
    _CACHE.move_to_end(k)
    while len(_CACHE) > CACHE_MAX:
        _CACHE.popitem(last=False)


async def _replay(model, content, reasoning, pt, ct):
    """Serve a cached answer as an OpenAI SSE stream."""
    cid, ts = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time())
    yield _sse(cid, ts, model, {"role": "assistant", "content": ""})
    if reasoning:
        yield _sse(cid, ts, model, {"reasoning_content": reasoning})
    yield _sse(cid, ts, model, {"content": content})
    yield f"data: {json.dumps({'id':cid,'object':'chat.completion.chunk','created':ts,'model':model,'choices':[{'index':0,'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':pt,'completion_tokens':ct,'total_tokens':pt+ct},'cached':True})}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/v1/models")
async def v1_models():
    return {"object": "list", "data": [{"id": k, "object": "model", "owned_by": "xai"}
                                       for k in [*MODELS, *LITE_MODELS, *PRO_MODELS, "grok-imagine"]]}


@app.post("/v1/chat/completions")
async def v1_completions(request: Request, authorization: str = Header(None)):
    key = check_key(authorization)
    if not key:
        raise HTTPException(401, "Invalid API key")

    body = await request.json()
    messages = body.get("messages", [])
    model, lite, pro = _resolve_model(body.get("model", "grok-auto"))
    concise = bool(body.get("concise", lite))  # extra fields (non-OpenAI): {"concise": true, "smart_prompt": false}
    style = {"concise": concise, "smart": bool(body.get("smart_prompt", get_setting("smart_prompt")))}
    pro = pro and not _has_images(messages)  # the review pass can't see images → plain answer for vision
    label = f"{model}-pro" if pro else model  # model name in logs / usage
    stream = body.get("stream", False)
    t0 = time.time()
    pt = est_tokens(_prompt_text(messages))

    # Cache: identical (model, style, pro, messages) within cache_ttl → reply without touching Grok. Opt out: {"cache": false}
    ck = _cache_key(label, style, messages) if body.get("cache", True) is not False else None
    hit = _cache_get(ck) if ck else None
    if hit:
        content, reasoning = hit
        ct = est_tokens(content)
        _log_usage("cache", key, label, int((time.time() - t0) * 1000), True, "", pt, ct, spend=False)
        if stream:
            return StreamingResponse(_replay(model, content, reasoning, pt, ct), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Cache": "HIT"})
        msg = {"role": "assistant", "content": content, **({"reasoning_content": reasoning} if reasoning else {})}
        return JSONResponse({"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion",
                             "created": int(time.time()), "model": model, "cached": True,
                             "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}},
                            headers={"X-Cache": "HIT"})

    if not pool:
        raise HTTPException(503, "No available account (none valid, or all out of grok-3 quota)")

    async def draft_then_review_msgs():
        """-pro: buffered draft, then the review conversation to run (falls back to the plain request)."""
        dmeta = {"out": "", "err": "", "email": ""}
        draft, _ = await grok_complete(messages, model, dmeta, style)
        if not draft:
            return messages, style
        _spend_quota(dmeta["email"])
        return _review_messages(messages, draft, concise), {"concise": concise, "smart": False}

    if stream:
        meta = {"out": "", "reasoning": "", "err": "", "email": ""}

        async def tracked_stream():
            msgs, st = await draft_then_review_msgs() if pro else (messages, style)
            async for chunk in _stream(msgs, model, meta, st):
                yield chunk
            ms = int((time.time() - t0) * 1000)
            _log_usage(meta.get("email") or "?", key, label, ms, not meta["err"], meta["err"], pt, est_tokens(meta["out"]))
            if ck and not meta["err"]:
                _cache_put(ck, meta["out"], meta["reasoning"])
        return StreamingResponse(tracked_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    meta = {"out": "", "err": "", "email": ""}
    msgs, st = await draft_then_review_msgs() if pro else (messages, style)
    content, reasoning = await grok_complete(msgs, model, meta, st)
    err = meta.get("err") or ("" if content else "Respon kosong dari Grok")
    ms = int((time.time() - t0) * 1000)
    ct = est_tokens(content)
    _log_usage(meta.get("email") or "?", key, label, ms, not err, err, pt, ct)
    if err:
        raise HTTPException(502, err)
    if ck:
        _cache_put(ck, content, reasoning)

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


@app.post("/v1/videos/generations")
async def v1_videos(request: Request, authorization: str = Header(None)):
    """Text-to-video via Grok Imagine (720p, ~6s, with audio). Body: prompt, aspect_ratio (1:1|2:3|3:2|9:16|16:9),
    duration (6|10, Grok may cap), response_format ("url" | "b64_json"). Each account makes 1 video / 24h."""
    key = check_key(authorization)
    if not key:
        raise HTTPException(401, "Invalid API key")
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt wajib diisi")
    aspect = body.get("aspect_ratio") or "9:16"
    if aspect not in ASPECTS:
        raise HTTPException(400, f"aspect_ratio harus salah satu dari {sorted(ASPECTS)}")
    duration = 10 if int(body.get("duration") or 6) >= 10 else 6
    last = "No available account"
    for _ in range(VIDEO_TRIES):
        acc = next_video_account()
        if not acc:
            break
        t0 = time.time()
        try:
            v = await grok_video(acc, prompt, aspect, duration)
        except VideoQuotaError as e:
            _vid_block[acc["email"]] = time.time() + 86400
            why = "klip stok" if isinstance(e, DecoyVideo) else "kuota habis"
            if isinstance(e, DecoyVideo):
                _log_usage(acc["email"], key, VIDEO_MODEL, int((time.time() - t0) * 1000), False, "decoy", spend=False)
            last = f"video akun gagal ({why}): {e}"
            log_event("warn", "api", f"video · {acc['email'].split('@')[0]} · {why}, ganti akun")
            continue
        except Exception as e:
            _log_usage(acc["email"], key, VIDEO_MODEL, int((time.time() - t0) * 1000), False, str(e), spend=False)
            code = 422 if "moderasi" in str(e) else 502
            raise HTTPException(code, str(e))
        _log_usage(acc["email"], key, VIDEO_MODEL, int((time.time() - t0) * 1000), True, spend=False)
        host = request.headers.get("x-forwarded-host") or request.url.netloc
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
        d = {"revised_prompt": v["prompt"], "x_grok": {k: v.get(k) for k in ("id", "width", "height", "resolution", "moderated")}}
        if body.get("response_format") == "b64_json":
            d["b64_json"] = base64.b64encode(v["bytes"]).decode()
        else:
            d["url"] = f"{scheme}://{host}/v1/media/{v['id']}.mp4"
        return {"created": int(time.time()), "data": [d]}
    raise HTTPException(503, last)


@app.get("/v1/media/{name}")
async def v1_media(name: str):
    # ponytail: public by unguessable uuid (like signed-URL-less CDNs); add auth if images must stay private
    if not re.fullmatch(r"[0-9a-f-]{36}\.(jpg|mp4)", name) or not (MEDIA_DIR / name).exists():
        raise HTTPException(404)
    return FileResponse(MEDIA_DIR / name, media_type="video/mp4" if name.endswith(".mp4") else "image/jpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


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


# ─── Auto-heal pool ─────────────────────────────────────
# ponytail: reuses the quota refresh (which already auth-checks each cookie) to spot dead cookies —
# no separate verify pass. Demotes them; optionally re-queues a login batch. Upgrade path: push-based
# health signals instead of polling, if request volume makes a 60-min sweep too coarse.

async def _autoheal_once():
    await admin_quota(refresh=1)  # fetch_quota flags genuinely-expired cookies with "kedaluwarsa" (NOT Cloudflare 403)
    dead = [e for e, q in QUOTA.items() if "kedaluwarsa" in q.get("error", "")]
    valid_emails = {a["email"] for a in pool}
    dead = [e for e in dead if e in valid_emails]
    if not dead:
        log_event("info", "check", f"Auto-heal: semua {len(pool)} akun sehat")
    else:
        with db() as c:
            c.executemany("UPDATE accounts SET valid=0 WHERE email=?", [(e,) for e in dead])
        reload_pool()
        log_event("warn", "check", f"Auto-heal: {len(dead)} cookie kedaluwarsa → dinonaktifkan · pool {len(pool)} akun")
    # Every sweep (not only when new ones died) so earlier expired sessions get retried too.
    if get_setting("autoheal_relogin") and not bulk_login.status()["running"]:
        _autoheal_relogin()


def _autoheal_relogin():
    """Queue a re-login batch for EXPIRED sessions only (had a cookie, now invalid) — never the
    never-attempted import backlog. Dead / wrong-password accounts are left alone."""
    with db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id,email,password,cookie_header FROM accounts "
            f"WHERE ({_BULK_SCOPES['expired']}) AND password!='' AND login_status NOT IN ('running','queued') "
            "ORDER BY id LIMIT 200")]
    if not rows:
        return
    conc = max(1, min(40, int(get_setting("concurrency"))))
    headless = bool(get_setting("headless"))
    proxies = [p.strip() for p in str(get_setting("proxies") or "").splitlines() if p.strip()]
    with _login_db_lock, db() as c:
        c.executemany("UPDATE accounts SET login_status='queued' WHERE id=?", [(r["id"],) for r in rows])
    log_event("info", "bulk", f"Auto-heal re-login: {len(rows)} akun · {conc} worker")

    def runner():
        bulk_login.run_bulk(rows, conc, headless, lambda a, r: _save_login(a, r, prune=False),
                            check_existing=True, login_timeout=int(get_setting("login_timeout")), proxies=proxies)
        reload_pool()

    threading.Thread(target=runner, daemon=True).start()


async def autoheal_loop():
    await asyncio.sleep(90)  # let startup + first quota warm settle
    while True:
        try:
            if get_setting("autoheal"):
                await _autoheal_once()
        except Exception as e:
            log_event("error", "system", f"Auto-heal error: {type(e).__name__}: {e}")
        await asyncio.sleep(max(5, int(get_setting("autoheal_interval_min") or 60)) * 60)


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
    "pending": "(valid=0 OR cookie_header='') AND login_status!='dead'",
    "dead": "login_status='dead'",  # blocked / errored DEAD_AFTER times — kept only for dedup, never retried
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
    name = row["email"].split("@")[0]
    state = _session_state(cookie) if cookie else False
    if state is None:  # Cloudflare / network — keep current status, don't demote a working account
        log_event("warn", "check", f"{name} · tak bisa dicek (Cloudflare) — status dipertahankan")
        return {"valid": bool(row["valid"]), "unknown": True}
    now = datetime.now(timezone.utc).isoformat()
    with db() as c:
        c.execute("UPDATE accounts SET valid=?, last_verified=? WHERE id=?", (1 if state else 0, now, aid))
    reload_pool()
    log_event("ok" if state else "warn", "check", f"{name} · cookie {'valid' if state else 'invalid'}")
    return {"valid": state}


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
    results, unknown = [], 0
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        state = _session_state(row["cookie_header"])
        if state is None:  # Cloudflare / network — leave this account's valid flag untouched
            unknown += 1
            continue
        with db() as c:
            c.execute("UPDATE accounts SET valid=?,last_verified=? WHERE id=?", (1 if state else 0, now, row["id"]))
        results.append({"id": row["id"], "valid": state})
    reload_pool()
    if unknown:
        log_event("warn", "check", f"Check-all: {unknown} akun tak bisa dicek (Cloudflare) — status dipertahankan")
    return {"checked": len(results), "unknown": unknown, "results": results}


# ─── Jio (Google AI Pro link generator — unrelated to Grok) ──────────────
# The jio-google-ai toolkit (full.js: buy number → check Jio → send OTP → login → get link) is the
# single source of truth: it owns its own data.db (table `accounts`). This app READS that db (JIO_DB,
# a separate file — no collision with Grok's accounts) and SPAWNS full.js for the generator, injecting
# the SMS provider key via env. No OTP/link logic is reimplemented here.
_JIO_PROVIDERS = {"grizzly", "smsbower"}
_JIO_BASE = {"grizzly": "https://api.grizzlysms.com/stubs/handler_api.php",
             "smsbower": "https://smsbower.page/stubs/handler_api.php"}
_JIO_KEY_SETTING = {"grizzly": "grizzly_key", "smsbower": "smsbower_key"}


def _jio_root() -> str:
    return os.environ.get("JIO_ROOT") or str(get_setting("jio_root") or "")


def _jio_db_path() -> str:
    p = os.environ.get("JIO_DB") or str(get_setting("jio_db") or "")
    if p:
        return p
    root = _jio_root()
    return os.path.join(root, "data.db") if root else ""


def _jio_conn():
    """Read-only connection to the toolkit's data.db, or None if not configured/missing."""
    p = _jio_db_path()
    if not p or not os.path.exists(p):
        return None
    # Path.as_uri() handles Windows drive/space encoding; append read-only mode.
    c = sqlite3.connect(Path(p).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _jio_rows(status: str | None = None) -> list[dict]:
    c = _jio_conn()
    if not c:
        return []
    try:
        q = "SELECT * FROM accounts" + (" WHERE status=?" if status else "") + " ORDER BY id DESC"
        return [dict(r) for r in (c.execute(q, (status,)) if status else c.execute(q))]
    except sqlite3.Error:
        return []
    finally:
        c.close()


def _jio_public(a: dict) -> dict:  # admin view — cookie stays hidden; otp/cost/error shown for the detail panel
    return {"id": a["id"], "phone": a.get("phone"), "jio_mobile": a.get("jio_mobile"), "status": a.get("status"),
            "hasLink": bool(a.get("link")), "link_at": a.get("link_at"), "updated_at": a.get("updated_at"),
            "created_at": a.get("created_at"), "cost": a.get("cost"), "otp": a.get("otp"),
            "activation_id": a.get("activation_id"), "error": a.get("error"),
            "go_key": _jio_go_key(a["id"]) if a.get("status") == "logged_in" else None}


def _jio_pick(acc_id: str | None = None) -> dict | None:
    pool = [a for a in _jio_rows("logged_in") if a.get("link")]
    if not pool:
        return None
    if acc_id is not None:
        return next((a for a in pool if str(a["id"]) == str(acc_id)), None)
    return max(pool, key=lambda a: a.get("link_at") or a.get("updated_at") or "")


def _jio_balance(provider: str):
    key = str(get_setting(_JIO_KEY_SETTING[provider]) or "")
    if not key:
        return None
    r = httpx.get(_JIO_BASE[provider], params={"api_key": key, "action": "getBalance"}, timeout=15)
    raw = r.text.strip()
    if raw.startswith("ACCESS_BALANCE:"):
        return float(raw.split(":")[1])
    return raw  # BAD_KEY etc. surfaced as-is


# Generator runs — one per SMS provider so several can run concurrently (grizzly + smsbower + future).
JIO_GENS: dict[str, dict] = {}                     # provider -> run state
_jio_gen_procs: dict[str, subprocess.Popen] = {}   # provider -> process
_jio_gen_lock = threading.Lock()
_JIO_UNLIMITED = 100000  # count=0 → run until the SMS balance runs out (full.js stops on NO_BALANCE)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")  # strip terminal color codes from toolkit log lines

# Keep-alive runs as its OWN pm2 process (name below), independent of this web app — so restarting the
# web never kills the Jio sessions. The backend only ensures it's up and reports its status.
JIO_KA_NAME = "jio-keepalive"

# Bulk link-health check — read-only probe (authJsonData loginFlag), NON-destructive (tak rotate JWT / tandai expired).
JIO_CHECK = {"running": False, "total": 0, "done": 0, "ok": 0, "dead": 0, "at": None}
JIO_HEALTH: dict = {}  # id -> {"alive": bool, "at": iso} dari cek terakhir


def _jio_check_one(aid: int) -> bool:
    acc = _jio_get(aid)
    ck = acc.get("cookie") if acc else None
    if not ck:
        return False
    try:
        r = httpx.get("https://www.jio.com/api/jio-authenticate-service/authenticate/authJsonData",
                      headers={"Cookie": ck, "Accept": "*/*", "Referer": "https://www.jio.com/selfcare/dashboard/",
                               "otype": "Z0241", "User-Agent": UA}, timeout=12)
        return r.status_code == 200 and str(r.json().get("loginFlag", "")).lower() == "true"
    except Exception:
        return False


def _jio_new_state(provider: str) -> dict:
    return {"running": False, "provider": provider, "target": 0, "done": 0, "unlimited": False,
            "started_at": None, "finished_at": None, "last": "", "lines": []}


def _jio_proxy_args(scope: str = "gen") -> list:
    """Per-scope proxy. scope 'gen' = generate (full.js, bisa butuh IP India buat beli nomor);
    'ka' = keep-alive/refresh (disarankan IP VPS yang stabil → sesi tak mati). [] = proxies.txt, ['--no-proxy'] = IP VPS."""
    key = "jio_use_proxy" if scope == "gen" else "jio_ka_proxy"
    return [] if get_setting(key) else ["--no-proxy"]


def _jio_env(provider: str) -> dict:
    env = dict(os.environ)
    for prov, setting in _JIO_KEY_SETTING.items():
        k = str(get_setting(setting) or "")
        if k:
            env[{"grizzly": "GRIZZLY_API_KEY", "smsbower": "SMSBOWER_API_KEY"}[prov]] = k
    p = _jio_db_path()
    if p:
        env["JIO_DB"] = p
    return env


def _jio_generate(count: int, concurrency: int, provider: str, unlimited: bool, extra: list):
    root = _jio_root()
    run_count = _JIO_UNLIMITED if unlimited else count
    st = _jio_new_state(provider)
    with _jio_gen_lock:
        st.update(running=True, target=run_count, unlimited=unlimited, started_at=time.time())
        JIO_GENS[provider] = st
    log_event("info", "jio", f"▶ Generator [{provider}]: target {'∞ (sampai saldo habis)' if unlimited else count} · {concurrency} worker")
    try:
        proc = subprocess.Popen(
            ["node", "bin/full.js", "--count", str(run_count), "--concurrency", str(concurrency),
             "--provider", provider, *extra],
            cwd=root, env=_jio_env(provider), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
    except Exception as e:
        with _jio_gen_lock:
            st.update(running=False, finished_at=time.time(), last=f"spawn gagal: {e}")
        log_event("error", "jio", f"Generator [{provider}] gagal start: {e}")
        return
    _jio_gen_procs[provider] = proc
    for line in proc.stdout:
        line = _ANSI.sub("", line.rstrip())
        if not line:
            continue
        m = re.search(r"\[(\d+)/(\d+)\]", line)
        with _jio_gen_lock:
            if m:
                st["done"] = int(m.group(1))
            st["last"] = line
            st["lines"] = (st["lines"] + [line])[-250:]
        log_event("ok" if "LINK:" in line else "info", "jio", f"[{provider}] {line}"[:300])
    proc.wait()
    with _jio_gen_lock:
        st.update(running=False, finished_at=time.time())
    log_event("info", "jio", f"■ Generator [{provider}] selesai: {st['done']} link")


# ─── Telegram report + archive watcher (link terakhir tak pernah hilang) ───
def _tg(text: str) -> bool:
    tok, chat = str(get_setting("jio_tg_token") or ""), str(get_setting("jio_tg_chat") or "")
    if not tok or not chat:
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                       data={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


_jio_prev_status: dict = {}  # id -> status terakhir dilihat (deteksi transisi)


def _jio_watch_loop():
    """Tiap ~60s: arsipkan link tiap sesi ke jio_links (anti-hilang), deteksi sesi logged_in→mati,
    catat ke Console + lapor Telegram (dengan link terakhir)."""
    time.sleep(50)
    first = True
    while True:
        try:
            rows = _jio_rows()
            now = datetime.now(timezone.utc).isoformat()
            deaths = []
            with db() as c:
                for a in rows:
                    aid, st, link, phone = a["id"], a.get("status"), a.get("link"), a.get("phone")
                    if link:  # arsip link terbaru (jangan timpa dgn kosong)
                        c.execute("INSERT INTO jio_links (jio_id,phone,link,link_at,last_status,updated_at) VALUES (?,?,?,?,?,?) "
                                  "ON CONFLICT(jio_id) DO UPDATE SET phone=excluded.phone, link=excluded.link, "
                                  "link_at=excluded.link_at, last_status=excluded.last_status, updated_at=excluded.updated_at",
                                  (aid, phone, link, a.get("link_at"), st, now))
                    prev = _jio_prev_status.get(aid)
                    if not first and prev == "logged_in" and st == "expired":
                        row = c.execute("SELECT link FROM jio_links WHERE jio_id=?", (aid,)).fetchone()
                        deaths.append((aid, phone, row["link"] if row else link))
                        c.execute("UPDATE jio_links SET died_at=? WHERE jio_id=?", (now, aid))
                    _jio_prev_status[aid] = st
            if deaths:
                alive = sum(1 for a in rows if a.get("status") == "logged_in")
                for aid, phone, lk in deaths:
                    log_event("warn", "jio", f"💀 Sesi #{aid} (+91{phone}) mati → expired. Link terakhir diarsip.")
                head = f"⚠️ {len(deaths)} sesi Jio mati (sisa hidup: {alive})\n\n"
                body = "\n\n".join(f"#{aid} +91{phone}\n{lk or '(tak ada link)'}" for aid, phone, lk in deaths[:10])
                _tg(head + body + ("" if len(deaths) <= 10 else f"\n\n…dan {len(deaths)-10} lagi"))
            first = False
        except Exception as e:
            log_event("error", "jio", f"watch: {type(e).__name__}: {e}")
        time.sleep(60)


@app.get("/admin/api/jio/stats")
def admin_jio_stats():
    c = _jio_conn()
    configured = c is not None
    rows = []
    if c:
        try:
            rows = [{"status": r["status"], "n": r["n"]} for r in
                    c.execute("SELECT status, COUNT(*) n FROM accounts GROUP BY status ORDER BY n DESC")]
        except sqlite3.Error:
            pass
        finally:
            c.close()
    return {"configured": configured, "db": _jio_db_path(),
            "live": next((s["n"] for s in rows if s["status"] == "logged_in"), 0), "stats": rows}


@app.get("/admin/api/jio/accounts")
def admin_jio_accounts(status: str | None = None, limit: int = 0):
    rows = _jio_rows(status)
    total = len(rows)
    if limit > 0:
        rows = rows[:limit]
    out = [_jio_public(a) for a in rows]
    claims = _jio_claims_map({a["id"] for a in out})  # merge buyer-claim counts + last health check + archived link
    arch = {}
    if out:
        with db() as c:
            q = ",".join("?" * len(out))
            arch = {r["jio_id"]: dict(r) for r in c.execute(f"SELECT jio_id, link, link_at FROM jio_links WHERE jio_id IN ({q})", [a["id"] for a in out])}
    for a in out:
        cl = claims.get(a["id"])
        a["claims"] = cl["claims"] if cl else 0
        a["claim_at"] = cl["last_at"] if cl else None
        h = JIO_HEALTH.get(a["id"])
        a["health"] = h["alive"] if h else None
        a["health_at"] = h["at"] if h else None
        ar = arch.get(a["id"])  # link terakhir yang diarsip (tetap ada walau sesi expired)
        a["saved_link"] = ar["link"] if ar else None
        a["saved_link_at"] = ar["link_at"] if ar else None
    return {"count": total, "accounts": out}


@app.get("/admin/api/jio/link")
def admin_jio_link(id: str | None = None):
    acc = _jio_pick(id)
    if not acc:
        raise HTTPException(503, "Belum ada session live dengan link — jalankan Generator dulu")
    return {"id": acc["id"], "phone": acc["phone"], "link": acc["link"], "link_at": acc.get("link_at")}


def _jio_write(sql: str, params=()):
    """Short read-write to the toolkit's data.db (for mark-used). Node keepalive may also write → busy timeout."""
    p = _jio_db_path()
    if not p or not os.path.exists(p):
        raise HTTPException(503, "data.db belum ada")
    c = sqlite3.connect(p, timeout=10)
    try:
        c.execute(sql, params)
        c.commit()
    finally:
        c.close()


def _jio_relink(aid: int) -> tuple[str, str]:
    """Re-mint a fresh link via relink.js. Returns (link, '') on success or ('', err)."""
    root = _jio_root()
    if not os.path.exists(os.path.join(root, "bin", "relink.js")):
        return "", "relink.js tidak ada di toolkit"
    try:
        r = subprocess.run(["node", "bin/relink.js", "--id", str(aid), *_jio_proxy_args("ka")], cwd=root,
                           env=_jio_env(str(get_setting("jio_provider") or "grizzly")),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"
    out = (r.stdout or "").strip()
    if r.returncode == 0 and out.startswith("OK "):
        return out[3:].strip(), ""
    return "", (r.stderr or out or "gagal").strip()


@app.post("/admin/api/jio/link/refresh")
def admin_jio_link_refresh(id: int):
    """Re-mint a FRESH link for a still-alive session. For when a handed-out link expired."""
    link, err = _jio_relink(id)
    if link:
        log_event("ok", "jio", f"#{id} link di-refresh")
        return {"id": id, "link": link}
    log_event("warn", "jio", f"#{id} refresh link gagal: {err[:80]}")
    raise HTTPException(502, f"gagal refresh: {err[:120]}")


@app.post("/admin/api/jio/used")
async def admin_jio_used(request: Request):
    """Mark a session as used: drop it from the live pool and from keep-alive (status → 'used')."""
    body = await request.json()
    aid = int(body.get("id") or 0)
    if not aid:
        raise HTTPException(400, "id wajib")
    _jio_write("UPDATE accounts SET status='used', updated_at=datetime('now') WHERE id=?", (aid,))
    log_event("info", "jio", f"#{aid} ditandai used")
    return {"ok": True, "id": aid}


@app.post("/admin/api/jio/bulk")
async def admin_jio_bulk(request: Request):
    """Aksi massal pada sesi terpilih. action: 'stop' (keluarkan dari poller/pool → status 'stopped'),
    'used' (tandai terpakai), 'check' (cek di latar: relink tiap sesi → mati jadi 'expired')."""
    body = await request.json()
    ids = [int(i) for i in (body.get("ids") or [])][:200]
    action = body.get("action")
    if not ids or action not in ("stop", "used", "check"):
        raise HTTPException(400, "ids + action (stop|used|check) wajib")
    if action in ("stop", "used"):
        status = "stopped" if action == "stop" else "used"
        q = ",".join("?" * len(ids))
        _jio_write(f"UPDATE accounts SET status='{status}', updated_at=datetime('now') WHERE id IN ({q})", ids)
        log_event("info", "jio", f"{len(ids)} sesi → {status}")
        return {"ok": True, "action": action, "n": len(ids)}

    todo = ids[:60]

    def run():  # cek = probe read-only (authJsonData); TIDAK rotate JWT / tandai expired → aman
        with _jio_gen_lock:
            JIO_CHECK.update(running=True, total=len(todo), done=0, ok=0, dead=0, at=None)
        for i in todo:
            alive = _jio_check_one(i)
            now = datetime.now(timezone.utc).isoformat()
            JIO_HEALTH[i] = {"alive": alive, "at": now}
            with _jio_gen_lock:
                JIO_CHECK["done"] += 1
                JIO_CHECK["ok" if alive else "dead"] += 1
        with _jio_gen_lock:
            JIO_CHECK.update(running=False, at=datetime.now(timezone.utc).isoformat())
        log_event("info", "jio", f"Cek link selesai: {JIO_CHECK['ok']} valid · {JIO_CHECK['dead']} perlu refresh (dari {len(todo)})")

    if JIO_CHECK["running"]:
        return {"started": False, "note": "cek lain sedang jalan"}
    threading.Thread(target=run, daemon=True).start()
    log_event("info", "jio", f"▶ Cek link {len(todo)} sesi di latar…")
    return {"started": True, "n": len(todo)}


@app.get("/admin/api/jio/check/status")
def admin_jio_check_status():
    with _jio_gen_lock:
        return {k: v for k, v in JIO_CHECK.items()}


# ─── Wrapped buyer link (ala drosek): 1 link permanen, auto-deteksi "diklaim" saat dibuka ───
_JIO_BOT_UA = ("telegrambot", "twitterbot", "facebookexternalhit", "whatsapp", "googlebot", "bingbot",
               "slackbot", "discordbot", "applebot", "vkshare", "crawler", "spider", "preview", "bot/")
_JIO_GO_FRESH = 720  # detik — kalau link tersimpan < 12 menit, redirect langsung (keepalive refresh tiap 10m)


def _jio_go_key(aid) -> str:
    sig = hmac.new(ADMIN_SECRET, f"jio{aid}".encode(), hashlib.sha256).hexdigest()[:12]
    return f"{aid}-{sig}"


def _jio_go_parse(key: str) -> int | None:
    try:
        aid, sig = key.rsplit("-", 1)
        if hmac.compare_digest(sig, hmac.new(ADMIN_SECRET, f"jio{aid}".encode(), hashlib.sha256).hexdigest()[:12]):
            return int(aid)
    except Exception:
        pass
    return None


def _jio_get(aid: int) -> dict | None:
    c = _jio_conn()
    if not c:
        return None
    try:
        r = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None
    except sqlite3.Error:
        return None
    finally:
        c.close()


def _jio_fresh(link_at: str | None) -> bool:
    if not link_at:
        return False
    try:
        t = datetime.fromisoformat(link_at.replace("Z", "+00:00").replace(" ", "T"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() < _JIO_GO_FRESH
    except ValueError:
        return False


def _jio_claims_map(ids=None) -> dict:
    with db() as c:
        rows = c.execute("SELECT jio_id, claims, first_at, last_at FROM jio_claims").fetchall()
    return {r["jio_id"]: dict(r) for r in rows if ids is None or r["jio_id"] in ids}


def _jio_record_claim(aid: int):
    now = datetime.now(timezone.utc).isoformat()
    with db() as c:
        c.execute("INSERT INTO jio_claims (jio_id,claims,first_at,last_at) VALUES (?,1,?,?) "
                  "ON CONFLICT(jio_id) DO UPDATE SET claims=claims+1, last_at=excluded.last_at", (aid, now, now))


def _jio_page(title: str, body: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=id><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>body{{font-family:system-ui,sans-serif;background:#0f172a;color:#f1f5f9;display:grid;"
        f"place-items:center;min-height:100vh;margin:0;padding:24px}}.c{{max-width:420px;text-align:center;background:#1e293b;"
        f"padding:32px;border-radius:16px}}h2{{margin:0 0 8px}}p{{color:#cbd5e1;line-height:1.6}}</style></head>"
        f"<body><div class=c>{body}</div></body></html>", status_code=code)


@app.get("/v1/jio/go/{key}")
def v1_jio_go(key: str, request: Request):
    """Link permanen untuk pembeli. Tiap dibuka: mint link fresh → redirect → catat 'diklaim'.
    Anti-crawler (biar preview chat tak memicu klaim palsu). Bagikan: {origin}/v1/jio/go/<key>."""
    aid = _jio_go_parse(key)
    if aid is None:
        return _jio_page("Link tidak valid", "<h2>Link tidak valid</h2><p>Periksa kembali link dari penjual.</p>", 400)
    ua = request.headers.get("user-agent", "").lower()
    if any(b in ua for b in _JIO_BOT_UA):  # anti-preview shield — jangan klaim karena bot chat
        return _jio_page("Aktivasi Google AI Pro", "<h2>Aktivasi Google AI Pro</h2><p>Buka link ini lewat browser untuk melanjutkan.</p>")
    acc = _jio_get(aid)
    if not acc or acc.get("status") != "logged_in":
        return _jio_page("Link kedaluwarsa", "<h2>Link tidak aktif</h2><p>Sesi sudah tidak tersedia. Hubungi penjual untuk link baru.</p>", 410)
    link = acc.get("link")
    if not _jio_fresh(acc.get("link_at")):  # mint ulang kalau link tersimpan sudah basi
        fresh, _ = _jio_relink(aid)
        link = fresh or link
    if not link:
        return _jio_page("Sedang menyiapkan", f"<h2>Sedang menyiapkan link…</h2><p>Coba muat ulang sebentar lagi.</p><a href='{request.url.path}' style='color:#38bdf8'>Muat ulang</a>", 503)
    _jio_record_claim(aid)
    log_event("ok", "jio", f"#{aid} diklaim pembeli (buka link)")
    return RedirectResponse(link, status_code=302)


# ─── Jio keep-alive — runs as its OWN pm2 process, independent of this web app ───
def _pm2(*args, timeout=20) -> subprocess.CompletedProcess:
    return subprocess.run(["pm2", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _jio_ka_status() -> dict:
    """Read the pm2-managed keepalive app's status (survives web restarts)."""
    try:
        r = _pm2("jlist")
        apps = json.loads(r.stdout or "[]")
    except Exception:
        return {"running": False, "managed": False, "last": "pm2 tak terbaca"}
    for a in apps:
        if a.get("name") == JIO_KA_NAME:
            pm = a.get("pm2_env", {})
            up = pm.get("pm_uptime")
            return {"running": pm.get("status") == "online", "managed": True,
                    "uptime_ms": (int(time.time() * 1000) - up) if (up and pm.get("status") == "online") else 0,
                    "restarts": pm.get("restart_time", 0), "last": f"status {pm.get('status')}"}
    return {"running": False, "managed": False, "last": "belum terdaftar di pm2"}


def _jio_ka_ensure() -> bool:
    """Start the keepalive pm2 app if the toolkit is present and it isn't already online. Idempotent."""
    root = _jio_root()
    if not root or not os.path.exists(os.path.join(root, "bin", "keepalive-all.js")):
        return False
    if _jio_ka_status().get("running"):
        return True
    interval = int(get_setting("jio_ka_interval") or 720)
    env = _jio_env(str(get_setting("jio_provider") or "grizzly"))
    try:
        # restart if registered-but-stopped, else start fresh. pm2 passes args after `--`.
        r = _pm2("describe", JIO_KA_NAME)
        if r.returncode == 0 and JIO_KA_NAME in (r.stdout or ""):
            _pm2("restart", JIO_KA_NAME, "--update-env", timeout=30)
        else:
            _pm2("start", os.path.join(root, "bin", "keepalive-all.js"), "--name", JIO_KA_NAME,
                 "--cwd", root, "--", "--interval", str(interval), *_jio_proxy_args("ka"), timeout=40)
        _pm2("save", timeout=20)
        log_event("info", "jio", f"Keep-alive pm2 '{JIO_KA_NAME}' dinyalakan")
        return True
    except Exception as e:
        log_event("error", "jio", f"Keep-alive pm2 gagal: {e}")
        return False


@app.post("/admin/api/jio/keepalive/start")
def admin_jio_keepalive_start():
    if not _jio_root() or not os.path.exists(os.path.join(_jio_root(), "bin", "keepalive-all.js")):
        raise HTTPException(400, "Toolkit belum siap (keepalive-all.js tidak ada)")
    return {"ok": _jio_ka_ensure(), **_jio_ka_status()}


@app.post("/admin/api/jio/keepalive/stop")
def admin_jio_keepalive_stop():
    try:
        _pm2("stop", JIO_KA_NAME); _pm2("save")
    except Exception:
        pass
    log_event("warn", "jio", "⏹ Keep-alive pm2 dihentikan")
    return _jio_ka_status()


@app.get("/admin/api/jio/keepalive/status")
def admin_jio_keepalive_status():
    return _jio_ka_status()


def _jio_logged_in_count() -> int:
    c = _jio_conn()
    if not c:
        return 0
    try:
        return c.execute("SELECT COUNT(*) FROM accounts WHERE status='logged_in'").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        c.close()


def _jio_auto_keepalive_loop():
    """Ensure the pm2 keepalive app is up whenever the pool has logged-in sessions (no UI toggle)."""
    time.sleep(45)  # let startup settle
    while True:
        try:
            if _jio_logged_in_count() > 0 and not _jio_ka_status().get("running"):
                _jio_ka_ensure()
        except Exception as e:
            log_event("error", "jio", f"auto-keepalive: {e}")
        time.sleep(60)


@app.get("/admin/api/jio/config")
def admin_jio_config():
    def mask(s):
        return (s[:3] + "…" + s[-2:]) if s and len(s) > 6 else ("••••" if s else "")
    gk, sk = str(get_setting("grizzly_key") or ""), str(get_setting("smsbower_key") or "")
    return {"provider": str(get_setting("jio_provider") or "grizzly"),
            "jio_key_set": bool(get_setting("jio_key")),
            "grizzly_set": bool(gk), "grizzly_mask": mask(gk),
            "smsbower_set": bool(sk), "smsbower_mask": mask(sk),
            "use_proxy": bool(get_setting("jio_use_proxy")), "ka_proxy": bool(get_setting("jio_ka_proxy")),
            "tg_set": bool(get_setting("jio_tg_token") and get_setting("jio_tg_chat")),
            "root": _jio_root(), "db": _jio_db_path(), "db_found": bool(_jio_conn())}


@app.post("/admin/api/jio/config")
async def admin_jio_config_save(request: Request):
    body = await request.json()
    # secrets: only overwrite when a non-empty value is sent (blank keeps the stored one)
    for field in ("grizzly_key", "smsbower_key"):
        if body.get(field):
            set_setting(field, str(body[field]).strip())
    # plain fields: set as given (empty is meaningful, e.g. jio_key="" disables the public endpoint)
    for field in ("jio_provider", "jio_key", "jio_root", "jio_db"):
        if field in body and body[field] is not None:
            set_setting(field, str(body[field]).strip())
    if "use_proxy" in body:
        set_setting("jio_use_proxy", 1 if body["use_proxy"] else 0)
    if "ka_proxy" in body:
        set_setting("jio_ka_proxy", 1 if body["ka_proxy"] else 0)
    for field in ("jio_tg_token", "jio_tg_chat"):  # token boleh kosong = matikan laporan
        if field in body and body[field] is not None:
            set_setting(field, str(body[field]).strip())
    return admin_jio_config()


@app.get("/admin/api/jio/balance")
def admin_jio_balance(provider: str = "grizzly"):
    if provider not in _JIO_PROVIDERS:
        raise HTTPException(400, "provider tidak dikenal")
    try:
        bal = _jio_balance(provider)
    except Exception as e:
        raise HTTPException(502, f"gagal cek saldo: {e}")
    if bal is None:
        raise HTTPException(400, f"API key {provider} belum diisi")
    return {"provider": provider, "balance": bal}


@app.post("/admin/api/jio/generate")
async def admin_jio_generate(request: Request):
    body = await request.json()
    provider = body.get("provider") or str(get_setting("jio_provider") or "grizzly")
    if provider not in _JIO_PROVIDERS:
        raise HTTPException(400, "provider tidak dikenal")
    if JIO_GENS.get(provider, {}).get("running"):
        return {"status": "already_running", "provider": provider}
    raw_count = int(body.get("count") or 0)
    unlimited = raw_count <= 0                       # 0 = jalan sampai saldo habis
    count = 0 if unlimited else min(1000, raw_count)
    concurrency = max(1, min(30, int(body.get("concurrency") or 1)))
    root = _jio_root()
    if not root or not os.path.exists(os.path.join(root, "bin", "full.js")):
        raise HTTPException(400, "Toolkit jio-google-ai tidak ditemukan — set JIO_ROOT (lihat Pengaturan)")
    if not str(get_setting(_JIO_KEY_SETTING[provider]) or ""):
        raise HTTPException(400, f"API key {provider} belum diisi")
    # proxy: per-run override (no_proxy True/False), else fall back to the jio_use_proxy setting.
    no_proxy = body.get("no_proxy")
    if no_proxy is None:
        pargs = _jio_proxy_args("gen")
    else:
        pargs = ["--no-proxy"] if no_proxy else []
    # fast: honor high concurrency + drop the inter-request gap (safe from a stable IP like the VPS).
    extra = pargs + (["--force-concurrency", "--rate", "0"] if body.get("fast") else [])
    threading.Thread(target=_jio_generate, args=(count, concurrency, provider, unlimited, extra), daemon=True).start()
    return {"status": "started", "count": count, "unlimited": unlimited, "concurrency": concurrency,
            "provider": provider, "proxy": not ("--no-proxy" in pargs), "fast": bool(body.get("fast"))}


@app.get("/admin/api/jio/generate/status")
def admin_jio_generate_status():
    """All provider runs: {runs: {provider: state+elapsed}, any_running: bool}."""
    runs = {}
    with _jio_gen_lock:
        for prov, st in JIO_GENS.items():
            s = dict(st)
            s["elapsed"] = round((s.get("finished_at") or time.time()) - s["started_at"]) if s.get("started_at") else 0
            runs[prov] = s
    return {"runs": runs, "any_running": any(s["running"] for s in runs.values()),
            "providers": sorted(_JIO_PROVIDERS)}


@app.post("/admin/api/jio/generate/stop")
async def admin_jio_generate_stop(request: Request):
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    target = body.get("provider")  # stop one provider, or all if omitted
    stopped = []
    for prov, p in list(_jio_gen_procs.items()):
        if target and prov != target:
            continue
        if p and p.poll() is None:
            p.terminate()
            stopped.append(prov)
    if stopped:
        log_event("warn", "jio", f"⏹ Generator dihentikan: {', '.join(stopped)}")
    return {"stopped": stopped}


@app.get("/v1/jio/link")
def v1_jio_link(request: Request, id: str | None = None):
    """Customer-facing: fresh Google AI link from the pool. Guard with the `jio_key` setting (header
    x-jio-key or ?key=). Empty jio_key → endpoint disabled so it is never accidentally public."""
    jkey = str(get_setting("jio_key") or "")
    if not jkey:
        raise HTTPException(404, "not found")
    if (request.headers.get("x-jio-key") or request.query_params.get("key")) != jkey:
        raise HTTPException(401, "unauthorized")
    acc = _jio_pick(id)
    if not acc:
        raise HTTPException(503, "no live session")
    return {"id": acc["id"], "phone": acc["phone"], "link": acc["link"],
            "generatedAt": datetime.now(timezone.utc).isoformat()}


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

# Account-level verdicts → login_status='dead' at once (row kept so a re-paste stays deduped, never retried).
_DEAD_RE = re.compile(r"been disabled|verify it.?s you|unusual|suspicious|suspended|banned|locked|deactivated", re.I)
# Our side broke (missing dep, our IP blocked, Cloudflare) → not the account's fault, don't count toward dead.
_INFRA_RE = re.compile(r"NotInstalledGeoIPExtra|Sign up with Google|Blocked due to abusive|Just a moment|cloudflare|"
                       r"Timeout \d+ms exceeded|TargetClosedError|NetworkError|net::ERR|launch the browser|InvalidIP", re.I)
DEAD_AFTER = 3  # non-password errors in a row before an account is marked dead


def _save_login(acc: dict, res: dict, prune: bool = False):
    """Persist one bulk-login result (called from worker threads → serialize writes).
    prune=True (bulk only): auto-delete wrong-password / failed accounts per settings del_invalid / del_failed."""
    global _last_pool_reload
    now = datetime.now(timezone.utc).isoformat()
    status = res.get("status", "error")
    # Auto-prune policy (bulk only). NB: 'failed' = timeout/error, which is often retryable — toggle in settings.
    # Failed (timeout/error) accounts are no longer deleted on the first try: they retry until DEAD_AFTER
    # account-level errors, or die at once on a block verdict (_DEAD_RE). del_failed now means "mark dead".
    detail = res.get("detail", "") or ""
    del_inv = prune and status == "invalid" and get_setting("del_invalid")
    deleted = del_inv
    dead = False
    with _login_db_lock, db() as c:
        if status == "success":
            c.execute("UPDATE accounts SET user_id=?,cookie_header=?,valid=1,last_verified=?,login_status='success',fail_count=0 WHERE id=?",
                      (res["user_id"], res["cookie_header"], now, acc["id"]))
        elif status == "skipped":
            c.execute("UPDATE accounts SET valid=1,last_verified=?,login_status='success',fail_count=0 WHERE id=?", (now, acc["id"]))
        elif deleted:
            c.execute("DELETE FROM accounts WHERE id=?", (acc["id"],))
        elif status == "invalid":
            c.execute("UPDATE accounts SET valid=0,login_status='invalid' WHERE id=?", (acc["id"],))
        else:
            infra = bool(_INFRA_RE.search(detail))
            n = (c.execute("SELECT fail_count FROM accounts WHERE id=?", (acc["id"],)).fetchone() or [0])[0] + (0 if infra else 1)
            dead = not infra and (bool(_DEAD_RE.search(detail)) or n >= DEAD_AFTER)
            st = "dead" if dead else ("failed" if status == "timeout" else f"error: {detail[:60]}")
            c.execute("UPDATE accounts SET login_status=?,fail_count=?" + (",valid=0,cookie_header=''" if dead else "") + " WHERE id=?",
                      (st, n, acc["id"]))
    # Throttled pool refresh so freshly logged-in accounts start serving during a long run.
    if status in ("success", "skipped") and time.time() - _last_pool_reload > 8:
        _last_pool_reload = time.time()
        reload_pool()
    name = acc["email"].split("@")[0]
    signup = bulk_login.STATE.get("worker") == "signup"
    src = "creator" if signup else "bulk"
    if deleted:
        log_event("warn", src, f"{name} · password salah → dihapus dari DB")
    elif dead:
        log_event("warn", src, f"{name} · akun mati (diblokir / error {DEAD_AFTER}x) → dikeluarkan dari pool, tidak dicoba lagi — {detail[:80]}")
    else:
        lvl = {"success": "ok", "skipped": "info", "invalid": "warn"}.get(status, "error")
        ok_label = "akun dibuat" if signup else "login OK"
        label = {"success": ok_label, "skipped": "cookie masih valid", "invalid": "password salah"}.get(status, "gagal")
        log_event(lvl, src, f"{name} · {label}{(' — ' + res.get('detail', '')) if status not in ('success', 'skipped') else ''}")


_BULK_SCOPES = {
    "new": "login_status IN ('idle','') AND valid=0",   # never attempted — dedup: skip anything already processed
    "pending": "(valid=0 OR cookie_header='') AND login_status!='dead'",  # never logged in / currently invalid
    "expired": "valid=0 AND cookie_header!='' AND login_status NOT IN ('dead','invalid')",  # session ran out
    "failed": "login_status='failed' OR login_status LIKE 'error%'",
    "invalid": "login_status='invalid'",               # wrong password — retry only if you fixed it
    "all": "login_status!='dead'",
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


@app.post("/admin/api/creator")
async def admin_creator(request: Request):
    """Create Grok accounts via 'Sign up with Google'. Body: {data: 'email:password' lines, concurrency?}.
    Imports the pasted accounts, then runs the concurrent signup worker; reuses the bulk-login status/stop."""
    if bulk_login.status()["running"]:
        return {"status": "already_running"}
    body = await request.json()
    accounts, dup, bad = parse_accounts(body.get("data", ""))
    if not accounts:
        return {"queued": 0, "duplicate_in_file": dup, "invalid_lines": bad}
    emails = [e for e, _ in accounts]
    # Dedup: import pasted accounts, then pick emails never processed OR that failed for a retryable reason.
    # Skipped: already created (valid), wrong Google password (invalid), dead. Temp-table JOIN avoids IN(...) var limit.
    with db() as c:
        c.executemany("INSERT OR IGNORE INTO accounts (email,password) VALUES (?,?)", accounts)
        c.execute("CREATE TEMP TABLE _paste (email TEXT PRIMARY KEY)")
        c.executemany("INSERT OR IGNORE INTO _paste (email) VALUES (?)", [(e,) for e in emails])
        rows = [dict(r) for r in c.execute(
            "SELECT a.id,a.email,a.password,a.cookie_header FROM accounts a JOIN _paste p ON a.email=p.email "
            "WHERE a.valid=0 AND (a.login_status IN ('idle','','failed') OR a.login_status LIKE 'error%')")]
        why = dict(c.execute(
            "SELECT CASE WHEN a.valid=1 THEN 'sudah jadi' WHEN a.login_status='invalid' THEN 'pw Google salah' "
            "WHEN a.login_status='dead' THEN 'akun mati' ELSE 'sedang antre' END k, COUNT(*) FROM accounts a "
            "JOIN _paste p ON a.email=p.email WHERE NOT (a.valid=0 AND (a.login_status IN ('idle','','failed') "
            "OR a.login_status LIKE 'error%')) GROUP BY k").fetchall())
    skipped = len(emails) - len(rows)
    detail = ", ".join(f"{n} {k}" for k, n in why.items())
    if not rows:
        return {"queued": 0, "skipped": skipped, "duplicate_in_file": dup, "invalid_lines": bad,
                "note": f"{skipped} email dilewati ({detail})"}
    conc = max(1, min(40, int(body.get("concurrency") or get_setting("concurrency"))))
    headless = bool(get_setting("headless"))
    proxies = [p.strip() for p in str(get_setting("proxies") or "").splitlines() if p.strip()]
    with _login_db_lock, db() as c:
        c.executemany("UPDATE accounts SET login_status='queued' WHERE id=?", [(r["id"],) for r in rows])
    log_event("info", "creator", f"▶ Creator (Sign up with Google) dimulai: {len(rows)} akun · {conc} worker · {len(proxies) or 'tanpa'} proxy · {skipped} dilewati{f' ({detail})' if detail else ''}")

    def runner():
        bulk_login.run_bulk(rows, conc, headless, lambda a, r: _save_login(a, r, prune=False),
                            check_existing=True, login_timeout=90, proxies=proxies, worker="signup")
        s = bulk_login.status()
        log_event("info", "creator", f"■ Creator selesai: {s['success']} dibuat · {s['invalid']} pw Google salah · {s['failed']} gagal")
        reload_pool()

    threading.Thread(target=runner, daemon=True).start()
    return {"queued": len(rows), "concurrency": conc, "skipped": skipped, "duplicate_in_file": dup, "invalid_lines": bad}


@app.get("/admin/api/accounts/bulk-login/status")
def admin_bulk_login_status():
    return bulk_login.status()


@app.post("/admin/api/accounts/bulk-login/stop")
def admin_bulk_login_stop():
    killed = bulk_login.request_stop()  # flag stop + force-kill in-flight Camoufox
    log_event("warn", "bulk", f"⏹ Stop paksa · {killed} proses Camoufox ditutup")

    def _cleanup():
        for _ in range(60):  # wait up to ~30s for the runner thread to drain, then free stuck rows
            if not bulk_login.status()["running"]:
                break
            time.sleep(0.5)
        with _login_db_lock, db() as c:
            n = c.execute("UPDATE accounts SET login_status='idle' WHERE login_status IN ('queued','running')").rowcount
        reload_pool()
        if n:
            log_event("info", "bulk", f"Stop: {n} akun antre direset ke idle (bisa dijalankan lagi)")

    threading.Thread(target=_cleanup, daemon=True).start()
    return {"ok": True, "camoufox_killed": killed}


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

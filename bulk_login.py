"""Concurrent bulk login runner for Grok accounts.

DB-agnostic: the caller passes `save_fn(acc, result)` and the selected accounts. Runs up to `concurrency`
Camoufox logins in parallel (threads — Camoufox is sync), skips accounts whose cookie is still valid,
and reports live progress via `status()`. Wrong-password accounts come back as status "invalid" and are skipped.
"""

import subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

from login_camoufox import login_account, signup_google, verify_cookies


def kill_camoufox() -> int:
    """Force-kill every running Camoufox (anti-detect Firefox) process. Returns how many were killed.
    ponytail: taskkill/pkill by image name — crude but reliable; these are our own short-lived children."""
    try:
        if sys.platform == "win32":
            # /T = kill child tree, /F = force. Camoufox launches as camoufox.exe.
            r = subprocess.run(["taskkill", "/F", "/T", "/IM", "camoufox.exe"],
                               capture_output=True, text=True)
            return r.stdout.count("SUCCESS")
        r = subprocess.run(["pkill", "-9", "-f", "camoufox"], capture_output=True)
        return 0 if r.returncode in (0, 1) else 0
    except Exception:
        return 0

WORKERS = {"login": login_account, "signup": signup_google}

_lock = threading.Lock()
STATE = {
    "running": False, "stop": False, "total": 0, "done": 0,
    "success": 0, "invalid": 0, "failed": 0, "skipped": 0,
    "concurrency": 0, "started_at": None, "finished_at": None,
    "current": [], "recent": [],  # current = emails in flight, recent = last 30 results
}


def status() -> dict:
    with _lock:
        s = dict(STATE)
        s["current"] = list(STATE["current"])
        s["recent"] = list(STATE["recent"])
        if STATE["started_at"]:
            elapsed = (STATE["finished_at"] or time.time()) - STATE["started_at"]
            s["elapsed"] = round(elapsed, 1)
            s["rate"] = round(STATE["done"] / elapsed * 60, 1) if elapsed > 0 and STATE["done"] else 0  # per minute
            left = STATE["total"] - STATE["done"]
            s["eta"] = round(left / (STATE["done"] / elapsed)) if STATE["done"] and left else 0  # seconds
        return s


def request_stop() -> int:
    """Hard stop: flag the run AND force-kill in-flight Camoufox so workers abort immediately
    (instead of waiting out each login_timeout). Returns Camoufox processes killed."""
    with _lock:
        STATE["stop"] = True
    return kill_camoufox()


def run_bulk(accounts: list[dict], concurrency: int, headless: bool, save_fn,
             check_existing: bool = True, login_timeout: int = 45, proxies: list[str] | None = None,
             worker: str = "login") -> None:
    """Blocking; run inside a daemon thread. `accounts` = [{id, email, password, cookie_header}].
    `worker`: 'login' (existing accounts) or 'signup' (create via Google OAuth).
    `proxies` (optional) round-robin per account."""
    work_fn = WORKERS.get(worker, login_account)
    proxies = [p for p in (proxies or []) if p.strip()]
    _ctr = {"i": 0}
    with _lock:
        STATE.update(running=True, stop=False, total=len(accounts), done=0, success=0, invalid=0,
                     failed=0, skipped=0, concurrency=concurrency, started_at=time.time(),
                     finished_at=None, current=[], recent=[], proxies=len(proxies), worker=worker)

    def work(acc):
        if STATE["stop"]:
            return
        email = acc["email"]
        with _lock:
            STATE["current"].append(email)
            proxy = None
            if proxies:
                proxy = proxies[_ctr["i"] % len(proxies)]
                _ctr["i"] += 1
                # "{sid}" in a proxy line = fresh sticky session per account (e.g. Maskify user-s-{sid}-ttl-900),
                # so every account gets its own exit IP instead of the whole batch sharing one (→ Cloudflare block).
                proxy = proxy.replace("{sid}", f"{acc.get('id', 0)}x{int(time.time()) % 100000}")
        res = {"status": "error", "detail": "unknown"}
        try:
            if check_existing and acc.get("cookie_header") and verify_cookies(acc["cookie_header"]):
                res = {"status": "skipped", "detail": "cookie masih valid"}
            else:
                res = work_fn(email, acc.get("password", ""), headless=headless,
                              timeout=login_timeout, proxy=proxy)
            # If a hard stop fired (Camoufox killed → work_fn may have returned garbage), don't persist:
            # the account wasn't really processed, so leave it untouched for the caller to reset.
            if not STATE["stop"]:
                save_fn(acc, res)
            else:
                res = {"status": "stopped", "detail": "dihentikan paksa"}
        except Exception as e:
            if STATE["stop"]:
                res = {"status": "stopped", "detail": "dihentikan paksa"}
            else:
                res = {"status": "error", "detail": f"{type(e).__name__}: {str(e)[:100]}"}
                try:
                    save_fn(acc, res)
                except Exception:
                    pass
        finally:
            with _lock:
                if email in STATE["current"]:
                    STATE["current"].remove(email)
                STATE["done"] += 1
                k = res.get("status", "error")
                bucket = k if k in ("success", "invalid", "skipped") else ("skipped" if k == "stopped" else "failed")
                STATE[bucket] += 1
                STATE["recent"] = ([{"email": email, "status": k, "detail": res.get("detail", "")}]
                                   + STATE["recent"])[:30]

    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            futs = [ex.submit(work, a) for a in accounts]
            for f in futs:
                if STATE["stop"]:
                    break
                try:
                    f.result()
                except Exception:
                    pass
    finally:
        with _lock:
            STATE.update(running=False, finished_at=time.time(), current=[])
        kill_camoufox()  # safety net: close any Camoufox left hanging by a crashed/timed-out worker

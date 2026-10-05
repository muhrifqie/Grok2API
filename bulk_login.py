"""Concurrent bulk login runner for Grok accounts.

DB-agnostic: the caller passes `save_fn(acc, result)` and the selected accounts. Runs up to `concurrency`
Camoufox logins in parallel (threads — Camoufox is sync), skips accounts whose cookie is still valid,
and reports live progress via `status()`. Wrong-password accounts come back as status "invalid" and are skipped.
"""

import threading, time
from concurrent.futures import ThreadPoolExecutor

from login_camoufox import login_account, verify_cookies

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


def request_stop():
    with _lock:
        STATE["stop"] = True


def run_bulk(accounts: list[dict], concurrency: int, headless: bool, save_fn,
             check_existing: bool = True, login_timeout: int = 45, proxies: list[str] | None = None) -> None:
    """Blocking; run inside a daemon thread. `accounts` = [{id, email, password, cookie_header}].
    `proxies` (optional) are assigned round-robin per account — essential for large runs since x.ai
    throttles many credential submissions from one IP."""
    proxies = [p for p in (proxies or []) if p.strip()]
    _ctr = {"i": 0}
    with _lock:
        STATE.update(running=True, stop=False, total=len(accounts), done=0, success=0, invalid=0,
                     failed=0, skipped=0, concurrency=concurrency, started_at=time.time(),
                     finished_at=None, current=[], recent=[], proxies=len(proxies))

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
        res = {"status": "error", "detail": "unknown"}
        try:
            if check_existing and acc.get("cookie_header") and verify_cookies(acc["cookie_header"]):
                res = {"status": "skipped", "detail": "cookie masih valid"}
            else:
                res = login_account(email, acc.get("password", ""), headless=headless,
                                    timeout=login_timeout, proxy=proxy)
            save_fn(acc, res)
        except Exception as e:
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
                STATE[k if k in ("success", "invalid", "skipped") else "failed"] += 1
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

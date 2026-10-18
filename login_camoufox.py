"""Grok login via Camoufox — extracts minimal cookies for API use."""

import json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from camoufox.sync_api import Camoufox

# Standalone-CLI test creds only. Real accounts live in the DB (managed via the admin panel / bulk import).
# Keep this empty in version control — do not commit real credentials.
CREDENTIALS: list[dict] = []

ACCOUNTS_FILE = Path(__file__).parent / "accounts.json"
NEEDED_COOKIES = {"sso", "x-userid"}


def log(msg, lvl="*"):
    print(f"[{datetime.now():%H:%M:%S}] [{lvl}] {msg}")


def verify_cookies(cookie_header: str) -> bool:
    if not cookie_header:
        return False
    try:
        r = httpx.get(
            "https://grok.com/api/auth/session",
            headers={"Cookie": cookie_header, "User-Agent": "Mozilla/5.0"},
            follow_redirects=True, timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("status") == "authenticated"
        # Cloudflare challenge (datacenter IP) can't be verified via httpx — assume still valid so we
        # don't needlessly re-login a working account. Genuine expiry returns 401 → False.
        if r.status_code == 403 and ("Just a moment" in r.text or "cf-" in r.text):
            return True
        return False
    except Exception:
        return False


def login_browser(email: str, password: str, headless: bool = False) -> dict | None:
    log(f"Browser login: {email}")

    with Camoufox(headless=headless) as browser:
        page = browser.new_page()
        page.goto(
            "https://accounts.x.ai/sign-in?redirect=grok-com&return_to=/&email=true",
            wait_until="networkidle",
        )
        time.sleep(2)

        # cookie banner
        try:
            btn = page.locator('button:has-text("Reject All")').first
            if btn.is_visible(timeout=3000):
                btn.click()
                time.sleep(1)
        except Exception:
            pass

        # email
        inp = page.locator('input[type="email"]')
        inp.wait_for(state="visible", timeout=10000)
        inp.fill(email)
        page.locator('button[type="submit"]:has-text("Next")').click()
        log("Email sent, waiting password field...")
        time.sleep(2)

        # password
        pw = page.locator('input[type="password"]')
        pw.wait_for(state="visible", timeout=10000)
        pw.fill(password)
        page.locator('button[type="submit"]').click()
        log("Submitted, waiting redirect...")

        # redirect
        try:
            page.wait_for_url(
                lambda u: "grok.com" in u or "accounts.x.ai/account" in u,
                timeout=30000,
            )
        except Exception:
            log(f"Timeout — stuck at {page.url}", "!")
            return None

        # navigate to grok.com for cookies
        if "grok.com" not in page.url:
            page.goto("https://grok.com/", wait_until="networkidle")
            time.sleep(3)

        # extract minimal cookies
        parts = {}
        for c in page.context.cookies():
            if c["name"] in NEEDED_COOKIES and "grok.com" in c["domain"]:
                parts[c["name"]] = c["value"]

        if "sso" not in parts:
            log("No SSO cookie!", "!")
            return None

        cookie_header = "; ".join(f"{k}={v}" for k, v in parts.items())

        # verify session
        session = page.evaluate(
            "async()=>{const r=await fetch('/api/auth/session');return r.ok?await r.json():null}"
        )
        if not session or session.get("status") != "authenticated":
            log("Session check failed!", "!")
            return None

        user_id = session["session"]["userId"]
        log(f"OK — {user_id[:8]}...", "+")

        return {
            "email": email,
            "password": password,
            "user_id": user_id,
            "cookie_header": cookie_header,
            "valid": True,
            "last_verified": datetime.now(timezone.utc).isoformat(),
        }


# x.ai renders the error as plain text on the page (NOT in a role=alert), e.g. "Wrong email address or password."
_ERR_CRED = re.compile(r"(wrong email address or password|incorrect (email|password)|invalid (email|password|credential)|couldn.?t find|no account|account (does.?n.?t|not) exist|email.*salah|password.*salah|tidak (ter)?daftar)", re.I)
_ERR_RATE = re.compile(r"(too many|try again later|rate.?limit|temporarily (blocked|locked)|slow down|unusual activity|verify you are human|captcha)", re.I)


def _dismiss_interstitial(page) -> bool:
    """x.ai sering menampilkan modal ToS/Acceptable-Use ('Got it') SETELAH login sukses yang menahan redirect.
    Klik tombolnya supaya alur lanjut. Hindari tombol cookie ('Accept All Cookies')."""
    for name in ("Got it", "I agree", "I Accept", "Agree", "Continue to Grok"):
        try:
            btn = page.get_by_role("button", name=name, exact=False).first
            if btn.count() and btn.is_visible(timeout=200):
                btn.click()
                return True
        except Exception:
            pass
    return False


def _await_turnstile(page, timeout: int = 20) -> bool:
    """x.ai menaruh Cloudflare Turnstile di form password. Tombol Login baru submit setelah token terisi —
    klik sebelum token siap = submit diam (gejala: form macet, sesi jadi anonim). Tunggu tokennya dulu."""
    try:
        page.wait_for_function(
            "() => { const i = document.querySelector('input[name=\"cf-turnstile-response\"], input[name*=\"turnstile\"], textarea[name*=\"turnstile\"]'); return i && i.value && i.value.length > 20; }",
            timeout=timeout * 1000)
        return True
    except Exception:
        return False


def _signed_in(page) -> bool:
    """Indikator sudah terautentikasi walau masih di accounts.x.ai (mis. ketahan modal ToS)."""
    try:
        return page.get_by_role("button", name=re.compile(r"sign ?out|log ?out|keluar", re.I)).first.is_visible(timeout=200)
    except Exception:
        return False


def _login_error(page) -> str:
    """'credential' (wrong pw / no account → skip), 'ratelimit' (retry later), or '' (none)."""
    try:
        body = page.locator("body").inner_text(timeout=800) or ""
    except Exception:
        return ""
    if _ERR_RATE.search(body):
        return "ratelimit"
    if _ERR_CRED.search(body):
        return "credential"
    return ""


_BLOCK_TYPES = {"image", "media", "font"}  # abort these → halaman jauh lebih cepat (Turnstile tetap jalan, butuh script saja)
_BLOCK_HOSTS = ("google-analytics.com", "googletagmanager.com", "doubleclick.net", "sentry.io",
                "mixpanel.com", "clarity.ms", "hotjar.com", "facebook.net", "adsct")


def _block_assets(page):
    """Blokir gambar/media/font + tracker supaya login/signup lebih cepat. Jangan blokir script/CSS/XHR (dibutuhkan form & Turnstile)."""
    def _route(route):
        try:
            req = route.request
            if req.resource_type in _BLOCK_TYPES or any(h in req.url for h in _BLOCK_HOSTS):
                return route.abort()
            return route.continue_()
        except Exception:
            try: return route.continue_()
            except Exception: return
    try:
        page.route("**/*", _route)
    except Exception:
        pass


def _parse_proxy(proxy: str | None):
    """Accepts 'host:port', 'http://host:port', 'user:pass@host:port', 'http://user:pass@host:port',
    or 'host:port:user:pass'. Returns a Camoufox proxy dict or None."""
    if not proxy or not proxy.strip():
        return None
    p = proxy.strip()
    scheme = "http"
    if "://" in p:
        scheme, p = p.split("://", 1)
    user = pw = None
    if "@" in p:
        cred, p = p.rsplit("@", 1)
        if ":" in cred:
            user, pw = cred.split(":", 1)
    bits = p.split(":")
    if len(bits) == 4 and user is None:  # host:port:user:pass
        host, port, user, pw = bits
    elif len(bits) >= 2:
        host, port = bits[0], bits[1]
    else:
        return None
    d = {"server": f"{scheme}://{host}:{port}"}
    if user:
        d["username"], d["password"] = user, pw
    return d


def login_account(email: str, password: str, headless: bool = True, timeout: int = 45,
                  proxy: str | None = None) -> dict:
    """Headless-friendly login used by the bulk runner.
    Returns {"status": "success"|"invalid"|"timeout"|"error", "detail", ...}.
    `invalid` = wrong password / unknown account → caller should skip it.
    `proxy` routes the browser through one IP (needed for bulk: x.ai throttles many logins per IP).
    Always closes the browser (Camoufox context manager), never raises."""
    cam_kwargs = {"headless": headless}
    pd = _parse_proxy(proxy)
    if pd:
        cam_kwargs["proxy"] = pd
        cam_kwargs["geoip"] = True  # align fingerprint locale/timezone to the proxy IP
    slow = bool(pd)  # proxy = beri tempo lebih (latency residensial)
    tmo = 30000 if slow else 15000
    try:
        with Camoufox(**cam_kwargs) as browser:
            page = browser.new_page()
            _block_assets(page)
            page.set_default_timeout(tmo)
            page.goto("https://accounts.x.ai/sign-in?redirect=grok-com&return_to=/&email=true",
                      wait_until="domcontentloaded", timeout=max(30000, tmo))

            try:
                btn = page.locator('button:has-text("Reject All")').first
                if btn.is_visible(timeout=3000):
                    btn.click()
            except Exception:
                pass

            inp = page.locator('input[type="email"]')
            inp.wait_for(state="visible", timeout=tmo)
            inp.fill(email)
            # Email step button is "Next"; password step adds an "Email sign-in code" button, so target by text.
            nxt = page.locator('button:has-text("Next")').first
            nxt.wait_for(state="visible", timeout=tmo)
            nxt.click()

            try:
                pw = page.locator('input[type="password"]')
                pw.wait_for(state="visible", timeout=tmo)
            except Exception:
                err = _login_error(page)
                if err == "credential":
                    return {"status": "invalid", "detail": "email ditolak / akun tidak ada"}
                if err == "ratelimit":
                    return {"status": "error", "detail": "rate-limit saat tahap email"}
                return {"status": "timeout", "detail": "field password tidak muncul"}

            pw.fill(password)
            # Tunggu token Cloudflare Turnstile sebelum submit.
            _await_turnstile(page, 25 if slow else 20)
            # Must be the password "Login" button, NOT the passwordless "Email sign-in code" button.
            login_btn = page.locator('button:has-text("Login")').first
            login_btn.click()
            # Klik pertama bisa jatuh sebelum token siap → retry sekali kalau form belum pindah.
            time.sleep(4)
            if "sign-in" in page.url and not _login_error(page):
                try:
                    if login_btn.is_visible(timeout=1500):
                        login_btn.click()
                except Exception:
                    pass

            deadline = time.time() + timeout
            while time.time() < deadline:
                url = page.url
                if "grok.com" in url or "accounts.x.ai/account" in url:
                    break
                err = _login_error(page)
                if err == "credential":
                    return {"status": "invalid", "detail": "password salah / akun tidak ada"}
                if err == "ratelimit":
                    return {"status": "error", "detail": "rate-limit / verifikasi manusia"}
                # Login sukses tapi ketahan modal ToS → klik 'Got it', lalu anggap selesai (session check menentukan).
                if _dismiss_interstitial(page):
                    time.sleep(1.5)
                    break
                if _signed_in(page):
                    break
                time.sleep(0.5)
            else:
                return {"status": "timeout", "detail": f"tidak redirect dalam {timeout}s"}

            if "grok.com" not in page.url:
                page.goto("https://grok.com/", wait_until="domcontentloaded")
                time.sleep(2)
                _dismiss_interstitial(page)  # ToS bisa muncul lagi di grok.com

            parts = {c["name"]: c["value"] for c in page.context.cookies()
                     if c["name"] in NEEDED_COOKIES and "grok.com" in c["domain"]}
            if "sso" not in parts:
                return {"status": "error", "detail": "login ok tapi cookie sso tidak ada"}
            cookie_header = "; ".join(f"{k}={v}" for k, v in parts.items())

            session = page.evaluate(
                "async()=>{const r=await fetch('/api/auth/session');return r.ok?await r.json():null}")
            uid = (session or {}).get("session", {}).get("userId", "") if session else ""
            if not session or session.get("status") != "authenticated" or not uid:
                # "authenticated" tanpa userId = sesi ANONIM (login password belum tuntas — ToS gate / Cloudflare).
                return {"status": "error", "detail": "sesi anonim / login belum tuntas (cek ToS / Cloudflare)"}

            return {"status": "success", "email": email, "user_id": uid, "cookie_header": cookie_header}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {str(e)[:120]}"}


# ─── Sign up via Google OAuth ───────────────────────────
# x.ai email sign-up is disabled; "Sign up with Google" works. Flow (same tab):
#   x.ai sign-up → accounts.google.com (identifier → Passwd) → Workspace ToS "I understand" (new accts)
#   → OAuth consent "Lanjutkan/Continue" → x.ai /oauth-complete → grok.com /tos-gate "Got it" → authenticated.
_CONSENT_BTNS = ["I understand", "Continue", "Lanjutkan", "I agree", "Agree", "Accept", "Terima", "Setuju",
                 "Allow", "Izinkan", "Confirm", "Konfirmasi", "Got it", "Mengerti", "Next", "Berikutnya"]
_GOOGLE_ERR = re.compile(r"(wrong password|couldn.?t find|couldn.?t sign you in|try again later|verify it.?s you|unusual|suspicious|been disabled|too many)", re.I)


def _click_btn(page, names) -> str | None:
    # Cepat: count() & is_visible() instan (tanpa wait). Jangan pakai is_visible(timeout=...) —
    # itu menunggu per-nama dan bisa membuang detik tiap iterasi loop saat tombol belum ada.
    for t in names:
        try:
            btn = page.get_by_role("button", name=t, exact=False).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=4000)
                return t
        except Exception:
            pass
    return None


def signup_google(email: str, password: str, headless: bool = True, timeout: int = 120, proxy: str | None = None) -> dict:
    """Create/attach a Grok account via 'Sign up with Google' using a Google (Workspace) email+password.
    Returns {status: success|invalid|error, user_id, cookie_header, email}. invalid = wrong Google password."""
    cam = {"headless": headless}
    pd = _parse_proxy(proxy)
    if pd:
        cam["proxy"] = pd
        cam["geoip"] = True
    try:
        with Camoufox(**cam) as browser:
            page = browser.new_page()
            _block_assets(page)
            page.set_default_timeout(20000)
            # Through a residential proxy the page is slow: a click before hydration does nothing and we'd
            # sit on x.ai until the identifier wait times out → longer goto, wait for load, re-click until Google.
            page.goto("https://accounts.x.ai/sign-up?redirect=grok-com&return_to=%2F", wait_until="load", timeout=60000)
            try:
                bb = page.locator('button:has-text("Reject All")').first
                if bb.is_visible(timeout=2500):
                    bb.click()
            except Exception:
                pass
            ident = page.locator('input[name="identifier"]')
            google = re.compile(r"accounts\.google\.")
            for _ in range(4):
                if google.search(page.url):
                    break
                try:
                    page.locator('button:has-text("Sign up with Google")').first.click(timeout=10000)
                    # wait_until="commit": URL change is enough — the Google page itself loads slowly via proxy
                    page.wait_for_url(google, timeout=20000, wait_until="commit")
                    break
                except Exception:
                    continue
            else:
                return {"status": "error", "detail": "x.ai: tombol Sign up with Google tidak membuka Google (proxy lambat?)"}

            # Google: email
            ident.wait_for(state="visible", timeout=45000)
            ident.fill(email)
            if not _click_btn(page, ["Next", "Berikutnya"]):
                page.keyboard.press("Enter")

            # Google: password. If it never shows, Google said something (unknown email, "verify it's you",
            # captcha…) → return that text so the caller can classify it (invalid / dead / retry).
            pwf = page.locator('input[type="password"]').first
            try:
                pwf.wait_for(state="visible", timeout=45000)
            except Exception:
                body = ""
                try:
                    body = re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=3000) or "")
                except Exception:
                    pass
                m = _GOOGLE_ERR.search(body)
                if m and ("wrong" in m.group(0).lower() or "find" in m.group(0).lower()):
                    return {"status": "invalid", "detail": f"Google: {m.group(0)}"}
                return {"status": "error", "detail": f"Google (kolom password tak muncul): {m.group(0) if m else body[:160]}"}
            time.sleep(0.5)
            pwf.fill(password)
            if not _click_btn(page, ["Next", "Berikutnya"]):
                page.keyboard.press("Enter")

            # ToS / OAuth consent loop until we land on grok.com
            deadline = time.time() + timeout
            while time.time() < deadline:
                if "grok.com" in page.url:
                    break
                body = ""
                try:
                    body = page.locator("body").inner_text(timeout=1500) or ""
                except Exception:
                    pass
                m = _GOOGLE_ERR.search(body)
                if m:
                    bad = m.group(0).lower()
                    if "wrong" in bad or "find" in bad:
                        return {"status": "invalid", "detail": f"Google: {m.group(0)}"}
                    return {"status": "error", "detail": f"Google: {m.group(0)}"}
                _click_btn(page, _CONSENT_BTNS)
                time.sleep(1.2)
            else:
                return {"status": "timeout", "detail": f"tidak sampai grok.com dalam {timeout}s"}

            if "grok.com" not in page.url:
                page.goto("https://grok.com/", wait_until="domcontentloaded")
                time.sleep(1.5)
            _click_btn(page, ["Got it"])

            parts = {c["name"]: c["value"] for c in page.context.cookies()
                     if c["name"] in NEEDED_COOKIES and "grok.com" in c["domain"]}
            # Cek session; ulangi sekali kalau kena race navigasi ("context destroyed").
            session, uid = None, ""
            for _ in range(2):
                try:
                    session = page.evaluate("async()=>{const r=await fetch('/api/auth/session');return r.ok?await r.json():null}")
                    uid = (session or {}).get("session", {}).get("userId", "") if session else ""
                    if uid:
                        break
                except Exception:
                    pass
                time.sleep(1.5)
                parts = {c["name"]: c["value"] for c in page.context.cookies()
                         if c["name"] in NEEDED_COOKIES and "grok.com" in c["domain"]}
            if "sso" not in parts or not uid:
                return {"status": "error", "detail": "OAuth selesai tapi cookie sso / userId tidak lengkap"}
            return {"status": "success", "email": email, "user_id": uid,
                    "cookie_header": "; ".join(f"{k}={v}" for k, v in parts.items())}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {str(e)[:120]}"}


def main():
    headless = "--headless" in sys.argv
    check_only = "--check" in sys.argv

    # load existing
    existing = {}
    if ACCOUNTS_FILE.exists():
        for a in json.loads(ACCOUNTS_FILE.read_text()):
            existing[a["email"]] = a

    results = []

    for cred in CREDENTIALS:
        email = cred["email"]

        # --check mode: just validate
        if check_only:
            ok = verify_cookies(existing.get(email, {}).get("cookie_header", ""))
            log(f"{email}: {'VALID' if ok else 'EXPIRED'}", "+" if ok else "!")
            if email in existing:
                existing[email]["valid"] = ok
                results.append(existing[email])
            continue

        # try saved cookies first
        if email in existing and existing[email].get("cookie_header"):
            log(f"Checking saved cookies for {email}...")
            if verify_cookies(existing[email]["cookie_header"]):
                log("Still valid, skipping browser", "+")
                existing[email]["valid"] = True
                existing[email]["last_verified"] = datetime.now(timezone.utc).isoformat()
                results.append(existing[email])
                continue
            log("Expired, re-login needed")

        # browser login
        try:
            result = login_browser(email, cred["password"], headless)
            if result:
                results.append(result)
        except Exception as e:
            log(f"Failed {email}: {e}", "!")

    if results:
        ACCOUNTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        log(f"Saved {len(results)} accounts to accounts.json", "+")

    print(f"\n{'='*40}")
    for r in results:
        s = "OK" if r.get("valid") else "EXPIRED"
        print(f"  {s} | {r['email']} | {r.get('user_id','?')[:8]}...")


if __name__ == "__main__":
    main()

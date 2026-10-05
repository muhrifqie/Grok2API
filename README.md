# Grok2API

> Turn Grok web accounts into an **OpenAI‑compatible API** — with a clean admin dashboard, concurrent account login automation, live console, usage/quota tracking, and image generation (Grok Imagine).

Grok2API is a reverse proxy that speaks the **OpenAI API dialect** (`/v1/chat/completions`, `/v1/models`, `/v1/images/generations`) on the front, and talks to the Grok web backend over its real‑time WebSocket on the back. Point any OpenAI SDK/client at it, use your pool of Grok accounts, and get chat, vision, and image generation through one familiar interface.

It ships with a modern **Next.js admin panel** (monochrome, Grok‑style) to manage the account pool, API keys, usage logs, live quota, a built‑in chat/image playground, and a live event console.

> ⚠️ **Unofficial.** Not affiliated with, endorsed by, or connected to xAI/Grok. For educational use with accounts you own. See [Disclaimer](#disclaimer).

---

## ✨ Features

- **OpenAI‑compatible** — drop‑in `base_url` for any OpenAI client/SDK. Streaming (SSE) and non‑streaming.
- **Chat + Vision** — send text and images (`image_url`, data‑URL or http); images are uploaded to Grok and attached.
- **Image generation** — `POST /v1/images/generations` via Grok Imagine (aspect ratios, Pro quality, `b64_json`/`url`).
- **Account pool** — round‑robin across many Grok accounts; exhausted‑quota accounts are skipped automatically.
- **Auto‑retry** — a completion that errors *before output* transparently retries on another account; non‑stream retries fully.
- **Concurrent login automation** — log in many accounts in parallel with [Camoufox](https://github.com/daijro/camoufox) (anti‑detect Firefox), handling Cloudflare Turnstile and the ToS gate. Wrong‑password/failed accounts can be auto‑pruned.
- **Admin dashboard (Next.js 16 + Tailwind v4)** — accounts (server‑side paginated), API keys, usage logs (CSV export), models & quota, a **Playground** (chat with markdown + image attach, and an Imagine tab), and a **live Console** that streams backend events.
- **Quota & token insight** — reads per‑account rate limits from Grok; estimates token usage (Grok doesn’t return token counts).
- **Cookie‑session admin auth** — HMAC‑signed cookie, password via env.

---

## 🧭 Architecture

```
                         ┌──────────────────────────────────────────┐
  OpenAI client ───────► │  FastAPI backend (server.py, :8168)       │
  (SDK / curl / app)     │                                           │
                         │   /v1/chat/completions  (stream + vision) │──┐
                         │   /v1/images/generations (Grok Imagine)   │  │  WebSocket / REST
                         │   /v1/models                              │  ├─────────────────►  grok.com
                         │   /admin/api/*  (cookie‑session auth)     │  │   (account cookies)
                         │   round‑robin account pool + retry        │──┘
                         └──────────────────────────────────────────┘
                                        ▲  proxied /admin/api & /v1
                         ┌──────────────┴───────────────────────────┐
  Admin (browser) ─────► │  Next.js admin‑ui (:3000)                 │
                         │  dashboard · accounts · keys · logs ·     │
                         │  models/quota · playground · console      │
                         └───────────────────────────────────────────┘

  Login automation:  Camoufox (headless Firefox) ──► accounts.x.ai sign‑in ──► extract sso + x‑userid cookies
```

- **Backend:** Python 3.11+ / FastAPI / Uvicorn / `websockets` / `httpx` / Camoufox. SQLite (`accounts`, `api_keys`, `usage_logs`, `settings`).
- **Frontend:** Next.js 16 (App Router, Turbopack), Tailwind v4, TypeScript. Proxies `/admin/api` and `/v1` to the backend via `next.config.ts` rewrites.

---

## 🤖 Models

The WS gateway takes a **mode** name. Public aliases map to what works on a free account:

| API model     | Grok mode | Notes |
|---------------|-----------|-------|
| `grok-auto`   | Auto      | Recommended — currently answers as Grok 4.5 |
| `grok-fast`   | Fast      | Faster, lighter |
| `grok-3`      | grok‑3    | Legacy id, same free quota |
| `gpt-4o`, `gpt-4` | → Auto | Compatibility aliases |
| `grok-3-mini`, `grok-2` | → Fast | Compatibility aliases |
| `grok-imagine`| Imagine   | Image generation (`/v1/images/generations`) |

> Free accounts share one grok‑3 quota window (per account). Modes like Expert/Heavy/Grok‑4/Build require signals a free account can’t pass through the proxy and are intentionally not mapped. Token counts in `usage` are **estimates** (~4 chars/token) — Grok does not return real token usage.

---

## 🚀 Quick start

### 1) Backend (API + admin API)

```bash
# Python 3.11+
pip install -r requirements.txt
python -m camoufox fetch           # one-time: download the anti-detect Firefox (needed for login automation)

export GROK2_ADMIN_PASS="change-me" # admin panel password (default: "123" — change it!)
python server.py --port 8168
```

### 2) Admin UI

```bash
cd admin-ui
npm install
# point the UI at the backend (default http://localhost:8168)
BACKEND_URL=http://localhost:8168 npm run dev      # dev on :3000
# or: npm run build && BACKEND_URL=http://localhost:8168 npm start
```

Open **http://localhost:3000**, log in with your admin password, then:
1. **Accounts → Bulk import** — paste `email:password` lines (separators `: | , ; tab` all work; duplicates dropped).
2. **Accounts → Bulk login** — log them in concurrently (set worker count & proxies in Settings).
3. **API Keys** — create a key for your apps.
4. **Playground / Console** — test and watch requests live.

---

## 🔌 API usage

Base URL: `http://localhost:8168/v1` (or the admin‑ui origin, which proxies `/v1`).

**Chat (stream):**
```bash
curl http://localhost:8168/v1/chat/completions \
  -H "Authorization: Bearer YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"model":"grok-auto","messages":[{"role":"user","content":"Hello!"}],"stream":true}'
```

**Vision (image in, OpenAI format):**
```bash
curl http://localhost:8168/v1/chat/completions \
  -H "Authorization: Bearer YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"model":"grok-auto","messages":[{"role":"user","content":[
        {"type":"text","text":"What is in this image?"},
        {"type":"image_url","image_url":{"url":"data:image/png;base64,...."}}]}]}'
```

**Image generation:**
```bash
curl http://localhost:8168/v1/images/generations \
  -H "Authorization: Bearer YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"prompt":"a cute orange cat, studio photo","n":2,"size":"1024x1024"}'
```

**Python (OpenAI SDK):**
```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8168/v1", api_key="YOUR_KEY")
print(client.chat.completions.create(
    model="grok-auto",
    messages=[{"role": "user", "content": "Explain reverse proxy simply"}],
).choices[0].message.content)
```

---

## ⚙️ Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `GROK2_ADMIN_PASS` | `123` | Admin panel password — **change in production** |
| `GROK2_HOST` | `0.0.0.0` | Bind host for the backend |
| `BACKEND_URL` | `http://localhost:8168` | Where admin‑ui proxies API calls (set for the Next app) |

Runtime settings (admin panel → **Settings**, stored in SQLite): login `concurrency` (default **5**), `headless`, `login_timeout`, rotating `proxies`, and auto‑delete toggles for wrong‑password / failed accounts.

See **[DEPLOY.md](DEPLOY.md)** for VPS (PM2 + nginx) and Docker instructions.

---

## 🖥️ Admin panel

- **Dashboard** — today’s requests, success rate, avg latency, tokens, grok‑3 quota, active pool.
- **Accounts** — server‑side pagination (handles thousands of accounts), search/sort/filter, per‑account quota meter, bulk import, and a concurrent **Bulk Login** panel with live progress & ETA.
- **Models & Quota** — live model catalog from Grok, per‑mode support, per‑account quota (chat + Imagine).
- **API Keys** — create/revoke, copy, usage counts, ready‑to‑paste curl.
- **Usage Logs** — recent requests with model/latency/tokens/status; CSV export.
- **Playground** — chat (markdown rendering, code copy, image attach, reasoning, stop/regenerate) + **Imagine** tab (aspect ratios, gallery, “use in chat”).
- **Console** — a live terminal of backend events (login, API, checks). Accumulates until you press Clear.

---

## 🔒 Security notes

- Change `GROK2_ADMIN_PASS`. Keep the backend port private (behind nginx / localhost) and expose only the admin‑ui, which proxies to it.
- `.admin_secret`, the SQLite DB, `*.har`, account lists, and generated media are **git‑ignored** — never commit them.
- Account passwords and cookies are stored in the local SQLite DB in plaintext; protect the host and the DB file.

---

## ⚠️ Disclaimer

This project is an **independent, unofficial** tool and is **not affiliated with, authorized, or endorsed by xAI or Grok**. It automates a browser login and proxies the Grok web interface. Using it may violate Grok/xAI’s Terms of Service and can get accounts rate‑limited or banned. It is provided **for educational and personal use with accounts you own**, with **no warranty**. You are solely responsible for how you use it, including compliance with all applicable terms and laws. The authors accept no liability.

## 📄 License

[MIT](LICENSE)

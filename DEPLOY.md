# Deploy Grok2API ke VPS (Ubuntu + PM2)

Panduan praktis deploy ke VPS Linux. Dua komponen:

- **Backend** — `server.py` (FastAPI + uvicorn), port **8168**, API OpenAI-compatible + admin API.
- **Admin UI** — `admin-ui/` (Next.js 16), port **3000**, proxy ke backend lewat `BACKEND_URL`.

Di produksi **hanya admin-ui yang publik**. Backend cukup dengerin localhost; admin-ui yang nge-proxy ke sana.

---

## a. Prasyarat

- **Python 3.11+** (dev box pakai 3.14; 3.11/3.12 juga jalan).
- **Node.js 20+** + npm.
- **PM2** global: `npm i -g pm2`.
- **Library sistem buat Firefox/Camoufox** (dibutuhin fitur login akun). Di Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y \
  libgtk-3-0 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 \
  libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 libxtst6 libxt6 \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libdbus-glib-1-2 libgbm1 libasound2 libpango-1.0-0 libpangocairo-1.0-0 \
  libcairo2 libgdk-pixbuf-2.0-0 libxkbcommon0 libxshmfence1 \
  fonts-liberation fonts-unifont xvfb xauth ca-certificates
```

> Catatan: di Ubuntu 24.04 (noble) paket `libasound2` namanya jadi `libasound2t64`. Kalau `playwright` kebetulan udah keinstal, `playwright install-deps firefox` juga nginstal set yang setara.

Setelah deps keinstal, download browser Camoufox-nya (sekali aja, agak besar):

```bash
python -m camoufox fetch   # atau: camoufox fetch
```

---

## b. Setup Backend

```bash
cd /path/ke/Grok2APIKU

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m camoufox fetch          # kalau belum (lihat bagian a)

cp .env.example .env              # lalu edit
export GROK2_ADMIN_PASS='password-kuat-anda'   # WAJIB ganti dari default "123"

python server.py --port 8168
```

Cek jalan:

```bash
curl http://localhost:8168/v1/models
```

Catatan: `grok2api.db`, folder `media/`, dan `.admin_secret` dibuat otomatis di folder project saat pertama jalan. Jangan hapus `.admin_secret` (kalau kehapus, semua sesi admin logout).

---

## c. Setup Admin UI

```bash
cd admin-ui
npm ci
npm run build

export BACKEND_URL='http://localhost:8168'
export PORT=3000
npm start
```

Buka `http://SERVER:3000/admin` (atau lewat domain, lihat bagian e).

---

## d. PM2 (jalanin dua-duanya)

File `ecosystem.config.js` udah nyiapin dua app. Edit dulu `interpreter` backend ke python venv (mis. `./.venv/bin/python3`) dan isi `GROK2_ADMIN_PASS` / `BACKEND_URL`.

```bash
cd /path/ke/Grok2APIKU
pm2 start ecosystem.config.js
pm2 save          # simpan daftar proses
pm2 startup       # ikutin instruksi yang muncul biar auto-start pas reboot
```

Cek & log:

```bash
pm2 status
pm2 logs grok2api-backend
pm2 logs grok2api-admin-ui
```

---

## e. Nginx reverse-proxy (domain → admin-ui :3000)

Cukup publish admin-ui. Streaming `/v1/*` (SSE) lewat Next.js, jadi matiin buffering di situ.

```nginx
server {
    listen 80;
    server_name panel.domain-anda.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Endpoint streaming chat — jangan di-buffer biar SSE ngalir realtime.
    location /v1/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }
}
```

Lalu `sudo nginx -t && sudo systemctl reload nginx`. Pasang HTTPS pakai `certbot --nginx -d panel.domain-anda.com`.

---

## f. Checklist keamanan

- [ ] **Ganti password admin**: set `GROK2_ADMIN_PASS` (default "123" — server kasih warning kalau belum diganti).
- [ ] **Jaga `.admin_secret`**: biarin auto-generate, jangan dihapus, jangan di-commit. Ini kunci HMAC sesi admin.
- [ ] **Firewall port 8168 ke localhost**: backend jangan kebuka ke internet. Jalanin di `127.0.0.1`, atau blokir dari luar:
      `sudo ufw deny 8168` (akses tetap jalan via admin-ui/nginx).
- [ ] **File sensitif jangan ke-commit**: `.env`, `.admin_secret`, `*.db`, `*.har`, `databaseakun*.txt`, `accounts.json*` sudah masuk `.gitignore`.
- [ ] **HTTPS + password panel** sebelum dibuka ke publik.

---

## Catatan Docker & Camoufox

`Dockerfile` (multi-stage) + `docker-compose.yml` disediakan untuk backend + admin-ui.

**Peringatan penting:** image backend **berat** karena harus bundle headless Firefox (Camoufox) buat fitur login akun — ratusan MB, butuh library X/GTK + Xvfb. Kalau di dalam container kamu **nggak** perlu fitur login (cuma pakai endpoint proxy `/v1/*`), hapus blok `apt-get` dan baris `RUN python -m camoufox fetch` di Dockerfile biar image jauh lebih kecil. Headless Firefox di Docker memang rewel (butuh virtual display); alternatif paling simpel buat fitur login tetap jalanin backend langsung di host (PM2, bagian d).

**Data runtime** (`grok2api.db`, `.admin_secret`, `media/`) di-mount lewat volume. Di host baru, bikin dulu biar Docker nge-mount file, bukan folder:

```bash
touch grok2api.db
python3 -c "import secrets; open('.admin_secret','w').write(secrets.token_hex(32))"
mkdir -p media
```

Jalanin:

```bash
export GROK2_ADMIN_PASS='password-kuat-anda'
docker compose up -d --build
```

Admin-ui di `http://SERVER:3000` (service `admin-ui` nyambung ke `backend` via jaringan compose, `BACKEND_URL=http://backend:8168`). Backend sengaja nggak di-publish ke host.

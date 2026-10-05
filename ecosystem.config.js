// PM2 process definitions for Grok2API (backend + admin UI).
// Run from the project root:
//   pm2 start ecosystem.config.js
//   pm2 save && pm2 startup
// Set the real GROK2_ADMIN_PASS / BACKEND_URL below or export them before starting.

module.exports = {
  apps: [
    {
      // FastAPI + uvicorn OpenAI-compatible proxy on :8168
      name: "grok2api-backend",
      script: "server.py",
      args: "--port 8168",
      // Use the project venv's python in production, e.g. "./.venv/bin/python3".
      // "python3" works if the deps are installed on the system interpreter.
      interpreter: "python3",
      cwd: __dirname, // server.py reads grok2api.db / media / .admin_secret relative to here
      autorestart: true,
      max_memory_restart: "500M",
      env: {
        GROK2_ADMIN_PASS: "change-me", // override before deploy
      },
    },
    {
      // Next.js admin dashboard on :3000 (proxies /admin/api/* and /v1/* to the backend)
      name: "grok2api-admin-ui",
      script: "npm",
      args: "start", // runs `next start`; requires `npm run build` first
      interpreter: "none", // execute npm directly, don't run it through node
      cwd: __dirname + "/admin-ui",
      autorestart: true,
      max_memory_restart: "500M",
      env: {
        PORT: "3000",
        BACKEND_URL: "http://localhost:8168",
      },
    },
  ],
};

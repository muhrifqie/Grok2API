"use client";

import { useState } from "react";
import { Bot, Loader2, LockKeyhole, LogIn } from "lucide-react";
import { cn } from "@/app/lib/utils";
import { ctl } from "@/app/components/ui";

export default function Login() {
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!pw) return setErr("Masukkan password admin");
    setBusy(true);
    setErr("");
    try {
      const res = await fetch("/admin/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ password: pw }),
      });
      if (res.ok) return void (window.location.href = "/");
      setErr(res.status === 401 ? "Password salah" : `Backend error (HTTP ${res.status})`);
    } catch {
      setErr("Backend tidak bisa dihubungi — pastikan server.py jalan di port 8168");
    }
    setBusy(false);
  }

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-bg px-4 py-10">
      <div className="mb-6 flex items-center gap-2.5">
        <Bot className="size-[34px] text-primary" strokeWidth={1.75} />
        <span className="text-xl font-bold tracking-tight text-text-main">Grok2API</span>
        <span className="rounded-full bg-primary px-1.5 py-[3px] text-[9px] font-bold uppercase leading-none tracking-wide text-primary-foreground">Admin</span>
      </div>
      <form onSubmit={submit} className="w-full max-w-sm border border-border-subtle bg-surface">
        <div className="flex items-center gap-3 border-b border-border-subtle px-5 py-4">
          <span aria-hidden className="grid size-10 shrink-0 place-items-center bg-primary/10 text-primary"><LockKeyhole className="size-5" /></span>
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.15em] text-primary">Panel admin</p>
            <h1 className="text-lg font-bold tracking-tight text-text-main">Masuk</h1>
          </div>
        </div>
        <div className="space-y-4 p-5">
          <label className="block">
            <span className="mb-1 block text-xs font-semibold text-text-main">Password admin</span>
            <input type="password" autoFocus autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="••••••••"
              aria-invalid={!!err} className={cn(ctl.input, "h-10 w-full", err && "border-danger/60")} />
            <span className="mt-1 block text-[11px] text-text-subtle">Diatur lewat env <code className="font-mono">GROK2_ADMIN_PASS</code> di server.</span>
          </label>
          {err && <p role="alert" className="border border-danger/30 bg-danger/5 px-3 py-2 text-xs font-medium text-danger">{err}</p>}
          <button type="submit" disabled={busy} className={cn(ctl.primary, "h-10 w-full")}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <LogIn className="size-4" />} {busy ? "Memeriksa…" : "Masuk"}
          </button>
        </div>
      </form>
      <p className="mt-4 text-xs text-text-subtle">Sesi berlaku 7 hari.</p>
    </div>
  );
}

"use client";

/** Panel LOGIN MASSAL — jalankan login concurrent untuk banyak akun, pantau progres live, atur proxy & worker. */
import { useEffect, useState } from "react";
import { Loader2, Play, Rocket, Settings2, Square, X, Zap } from "lucide-react";
import { api } from "@/app/lib/api";
import { bulkRes, statsRes, useRes, type Settings } from "@/app/lib/store";
import { cn, fmt } from "@/app/lib/utils";
import { toast } from "@/app/components/toast";
import { Chip, Field, Modal, Section, ctl, pillCls } from "@/app/components/ui";

const SCOPES = [
  { v: "pending", label: "Belum login" },
  { v: "failed", label: "Gagal/error" },
  { v: "invalid", label: "Password salah" },
  { v: "all", label: "Semua" },
];
const fmtDur = (s = 0) => (s >= 3600 ? `${Math.floor(s / 3600)}j ${Math.floor((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}d` : `${Math.round(s)}d`);
const STAT_TONE: Record<string, string> = { success: "text-success", skipped: "text-text-muted", invalid: "text-warning", failed: "text-danger", error: "text-danger", timeout: "text-danger" };

export function BulkLogin() {
  const bulk = useRes(bulkRes);
  const b = bulk.data;
  const stats = useRes(statsRes).data;
  const [scope, setScope] = useState("pending");
  const [limit, setLimit] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [cfg, setCfg] = useState<Settings | null>(null);
  const [savingCfg, setSavingCfg] = useState(false);

  // Percepat polling status saat run aktif (resource default 2s sudah pas).
  useEffect(() => { if (b?.running) void statsRes.refresh(); }, [b?.done, b?.running]);

  async function start() {
    try {
      const r = await api<{ queued: number; concurrency: number; proxies: number }>("/admin/api/accounts/bulk-login", {
        method: "POST", body: JSON.stringify({ scope, limit: limit ? Number(limit) : 0 }),
      });
      if (!r.queued) return toast.info("Tidak ada akun yang cocok dengan scope ini");
      toast.success(`${fmt(r.queued)} akun diproses · ${r.concurrency} worker${r.proxies ? ` · ${r.proxies} proxy` : " · tanpa proxy"}`);
      void bulkRes.refresh();
    } catch (e) { toast.error((e as Error).message); }
  }
  async function stop() {
    await api("/admin/api/accounts/bulk-login/stop", { method: "POST" });
    toast.info("Menghentikan setelah worker aktif selesai…");
  }
  async function openSettings() {
    try { setCfg(await api<Settings>("/admin/api/settings")); setShowSettings(true); }
    catch (e) { toast.error((e as Error).message); }
  }
  async function saveSettings() {
    if (!cfg) return;
    setSavingCfg(true);
    try { await api("/admin/api/settings", { method: "POST", body: JSON.stringify(cfg) }); toast.success("Pengaturan disimpan"); setShowSettings(false); }
    catch (e) { toast.error((e as Error).message); }
    finally { setSavingCfg(false); }
  }

  const running = !!b?.running;
  const pct = b && b.total ? Math.round((b.done / b.total) * 100) : 0;
  const pendingCount = stats?.invalid ?? 0;

  return (
    <Section icon={Rocket} tone="primary" title="Login massal"
      desc="Login banyak akun sekaligus (concurrent). Password salah otomatis dilewati."
      actions={<button type="button" onClick={openSettings} className={ctl.ghost}><Settings2 className="size-4" /> Pengaturan</button>}>
      {running ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3 text-sm">
            <span className="flex items-center gap-2 font-semibold"><Loader2 className="size-4 animate-spin text-primary" /> Memproses… {fmt(b!.done)}/{fmt(b!.total)}</span>
            <button type="button" onClick={stop} className={ctl.danger}><Square className="size-4" /> Stop</button>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${pct}%` }} />
          </div>
          <div className="flex flex-wrap gap-1.5 text-xs">
            <Chip tone="success">{fmt(b!.success)} berhasil</Chip>
            <Chip tone="muted">{fmt(b!.skipped)} dilewati</Chip>
            <Chip tone="warning">{fmt(b!.invalid)} pw salah</Chip>
            <Chip tone="danger">{fmt(b!.failed)} gagal</Chip>
            <span className="flex-1" />
            <Chip>{b!.concurrency} worker{b!.proxies ? ` · ${b!.proxies} proxy` : ""}</Chip>
            {!!b!.rate && <Chip title="akun per menit">{b!.rate}/mnt</Chip>}
            {!!b!.eta && <Chip title="perkiraan sisa waktu">sisa ~{fmtDur(b!.eta)}</Chip>}
          </div>
          {!!b!.current.length && <p className="truncate font-mono text-[11px] text-text-subtle">aktif: {b!.current.slice(0, 3).join(", ")}{b!.current.length > 3 ? ` +${b!.current.length - 3}` : ""}</p>}
          {!!b!.recent.length && (
            <div className="max-h-32 space-y-0.5 overflow-y-auto border-t border-border-subtle pt-2 text-[11px]">
              {b!.recent.slice(0, 12).map((r, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className={cn("w-14 shrink-0 font-semibold", STAT_TONE[r.status] ?? "text-text-muted")}>{r.status}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-text-muted">{r.email}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {b && b.total > 0 && (
            <p className="flex flex-wrap items-center gap-1.5 text-xs text-text-muted">
              Run terakhir: <Chip tone="success">{fmt(b.success)} ok</Chip> <Chip tone="muted">{fmt(b.skipped)} skip</Chip>
              <Chip tone="warning">{fmt(b.invalid)} pw salah</Chip> <Chip tone="danger">{fmt(b.failed)} gagal</Chip>
              {!!b.elapsed && <span>dalam {fmtDur(b.elapsed)}</span>}
            </p>
          )}
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <p className="mb-1.5 text-xs font-semibold text-text-main">Target</p>
              <div className="flex flex-wrap gap-1.5">
                {SCOPES.map((s) => <button key={s.v} type="button" onClick={() => setScope(s.v)} className={pillCls(scope === s.v)}>{s.label}</button>)}
              </div>
            </div>
            <Field label="Batas (kosong = semua)">
              <input value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ""))} placeholder="mis. 50" className={cn(ctl.input, "w-28")} />
            </Field>
            <button type="button" onClick={start} className={cn(ctl.primary, "h-9")}><Play className="size-4" /> Mulai login</button>
          </div>
          <p className="flex items-start gap-2 border border-warning/30 bg-warning/5 px-3 py-2 text-[11px] text-text-muted">
            <Zap className="mt-px size-3.5 shrink-0 text-warning" />
            <span>x.ai membatasi jumlah login per IP. Tanpa proxy, pakai worker kecil (1–3) dan jumlah terbatas, atau banyak login akan timeout. Untuk ribuan akun, isi daftar proxy di <b className="text-text-main">Pengaturan</b> ({pendingCount ? `${fmt(pendingCount)} akun belum valid` : "semua akun valid"}).</span>
          </p>
        </div>
      )}

      {showSettings && cfg && (
        <Modal title="Pengaturan login massal" icon={Settings2} onClose={() => setShowSettings(false)}
          footer={<>
            <button type="button" onClick={() => setShowSettings(false)} className={ctl.btn}>Batal</button>
            <button type="button" onClick={saveSettings} disabled={savingCfg} className={ctl.primary}>{savingCfg && <Loader2 className="size-4 animate-spin" />} Simpan</button>
          </>}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Worker (concurrent)" hint="1–40. Tanpa proxy: 1–3.">
              <input type="number" min={1} max={40} value={cfg.concurrency} onChange={(e) => setCfg({ ...cfg, concurrency: Number(e.target.value) })} className={cn(ctl.input, "w-full")} />
            </Field>
            <Field label="Timeout (detik)" hint="Per akun, 20–90.">
              <input type="number" min={20} max={120} value={cfg.login_timeout} onChange={(e) => setCfg({ ...cfg, login_timeout: Number(e.target.value) })} className={cn(ctl.input, "w-full")} />
            </Field>
          </div>
          <label className="flex cursor-pointer items-center gap-2.5 text-sm">
            <input type="checkbox" checked={!!cfg.headless} onChange={(e) => setCfg({ ...cfg, headless: e.target.checked ? 1 : 0 })} className="size-4 accent-[var(--primary)]" />
            <span><b className="font-semibold text-text-main">Headless</b> <span className="text-xs text-text-muted">(browser tak terlihat — lebih cepat & hemat)</span></span>
          </label>
          <div className="space-y-2 border-t border-border-subtle pt-3">
            <p className="text-xs font-semibold text-text-main">Auto-hapus saat login massal</p>
            <label className="flex cursor-pointer items-start gap-2.5 text-sm">
              <input type="checkbox" checked={!!cfg.del_invalid} onChange={(e) => setCfg({ ...cfg, del_invalid: e.target.checked ? 1 : 0 })} className="mt-0.5 size-4 accent-[var(--primary)]" />
              <span>Hapus akun <b className="font-semibold text-text-main">password salah</b> <span className="text-xs text-text-muted">(aman — kredensial memang invalid)</span></span>
            </label>
            <label className="flex cursor-pointer items-start gap-2.5 text-sm">
              <input type="checkbox" checked={!!cfg.del_failed} onChange={(e) => setCfg({ ...cfg, del_failed: e.target.checked ? 1 : 0 })} className="mt-0.5 size-4 accent-[var(--warning)]" />
              <span>Hapus akun <b className="font-semibold text-text-main">gagal login</b> (timeout/error)
                <span className="mt-0.5 block text-xs text-warning">⚠ timeout sering retryable — akun valid bisa ikut terhapus. Matikan kalau mau retry dulu.</span></span>
            </label>
          </div>
          <Field label="Daftar proxy (satu per baris)" hint="Format: host:port · user:pass@host:port · http://user:pass@host:port · host:port:user:pass. Di-rotasi per akun.">
            <textarea rows={5} value={cfg.proxies} onChange={(e) => setCfg({ ...cfg, proxies: e.target.value })}
              placeholder={"http://user:pass@1.2.3.4:8080\n5.6.7.8:3128:user:pass"} className={cn(ctl.input, "h-auto w-full resize-y py-2 font-mono text-[12px]")} />
          </Field>
          <p className="text-[11px] text-text-subtle">{cfg.proxies.split("\n").filter((l) => l.trim()).length} proxy terdaftar.</p>
        </Modal>
      )}
    </Section>
  );
}

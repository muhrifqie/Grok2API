"use client";

import { Suspense, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Activity, Check, CircleCheck, Code2, Copy, KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { api, fmtTime, relTime } from "@/app/lib/api";
import { keysRes, useRes, type ApiKey } from "@/app/lib/store";
import { cn, fmt } from "@/app/lib/utils";
import { toast } from "@/app/components/toast";
import { Chip, Empty, ErrorBox, Field, Modal, PageHead, RefreshBtn, Section, Skel, Stat, ctl, tbl } from "@/app/components/ui";

const mask = (k: string) => (k.length > 16 ? `${k.slice(0, 9)}••••${k.slice(-4)}` : k);
const useOrigin = () => useSyncExternalStore(() => () => {}, () => location.origin, () => "http://localhost:3000");

async function copy(v: string, what = "Key") {
  try { await navigator.clipboard.writeText(v); toast.success(`${what} disalin`); }
  catch { toast.error("Gagal menyalin"); }
}

function KeysView() {
  const sp = useSearchParams();
  const router = useRouter();
  const res = useRes(keysRes);
  const keys = useMemo(() => res.data ?? [], [res.data]);
  const [modal, setModal] = useState(!!sp.get("new"));
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [del, setDel] = useState<ApiKey | null>(null);
  const [deleting, setDeleting] = useState(false);
  const base = useOrigin();

  useEffect(() => { if (sp.get("new")) setModal(true); }, [sp]);
  const closeNew = () => { setModal(false); if (sp.get("new")) router.replace("/keys"); };

  async function create() {
    setSaving(true);
    try {
      const r = await api<{ key: string }>("/admin/api/keys", { method: "POST", body: JSON.stringify({ name: name.trim() || "Tanpa nama" }) });
      setCreated(r.key);
      setName("");
      closeNew();
    } catch (e) { toast.error((e as Error).message); }
    finally { setSaving(false); void keysRes.refresh(); }
  }

  async function remove(k: ApiKey) {
    setDeleting(true);
    try {
      await api(`/admin/api/keys/${k.id}`, { method: "DELETE" });
      toast.success(`Key "${k.name}" dihapus`);
      setDel(null);
    } catch (e) { toast.error((e as Error).message); }
    finally { setDeleting(false); void keysRes.refresh(); }
  }

  const sample = keys.find((k) => k.active)?.key ?? "grok2api";
  const curl = `curl ${base}/v1/chat/completions \\\n  -H "Authorization: Bearer ${mask(sample)}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"grok-auto","messages":[{"role":"user","content":"Halo"}],"stream":true}'`;

  return (
    <div className="space-y-5">
      <PageHead
        icon={KeyRound}
        eyebrow="Akses API"
        title="API Keys"
        desc={<>Key dipakai di header <code className="bg-surface-2 px-1 font-mono text-[12px]">Authorization: Bearer …</code> setiap request ke endpoint OpenAI-compatible.</>}
        actions={
          <>
            <RefreshBtn onClick={() => keysRes.refresh()} busy={res.validating} />
            <button type="button" onClick={() => setModal(true)} className={ctl.primary}><Plus className="size-4" /> Buat key</button>
          </>
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {!res.data ? Array.from({ length: 3 }, (_, i) => <Skel key={i} className="h-[92px]" />) : (
          <>
            <Stat label="Total key" icon={KeyRound} tone="primary" value={fmt(keys.length)} />
            <Stat label="Aktif" icon={CircleCheck} tone="success" value={fmt(keys.filter((k) => k.active).length)} hint="bisa dipakai request" />
            <Stat label="Total request" icon={Activity} tone="info" value={fmt(keys.reduce((s, k) => s + k.request_count, 0))} className="col-span-2 sm:col-span-1" />
          </>
        )}
      </div>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_26rem]">
        {!res.data ? (
          res.error ? <Section><ErrorBox msg={res.error} onRetry={() => keysRes.refresh()} /></Section>
            : <Section flush><div className="space-y-px p-1">{Array.from({ length: 3 }, (_, i) => <Skel key={i} className="h-12" />)}</div></Section>
        ) : !keys.length ? (
          <Section>
            <Empty emoji="🔑" title="Belum ada API key" action={<button type="button" onClick={() => setModal(true)} className={ctl.primary}><Plus className="size-4" /> Buat key</button>}>
              Tanpa key, semua request ke /v1 ditolak (401).
            </Empty>
          </Section>
        ) : (
          <Section icon={KeyRound} title="Daftar key" desc="Klik key untuk menyalin" flush>
            <div className={tbl.wrap}>
              <table className={cn(tbl.table, "min-w-[640px]")}>
                <thead>
                  <tr>
                    <th className={tbl.th}>Nama</th>
                    <th className={tbl.th}>Key</th>
                    <th className={tbl.th}>Status</th>
                    <th className={cn(tbl.th, "text-right")}>Request</th>
                    <th className={tbl.th}>Dibuat</th>
                    <th className={cn(tbl.th, "text-right")}>Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {keys.map((k) => (
                    <tr key={k.id} className={tbl.row}>
                      <td className={cn(tbl.td, "font-semibold")}>{k.name || "—"}</td>
                      <td className={tbl.td}>
                        <button type="button" onClick={() => copy(k.key)} title="Salin key lengkap" className="inline-flex items-center gap-1.5 font-mono text-[13px] text-text-main hover:text-primary">
                          {mask(k.key)} <Copy className="size-3.5 text-text-subtle" />
                        </button>
                      </td>
                      <td className={tbl.td}>{k.active ? <Chip tone="success">Aktif</Chip> : <Chip>Nonaktif</Chip>}</td>
                      <td className={cn(tbl.td, tbl.num, "font-semibold")}>{fmt(k.request_count)}</td>
                      <td className={cn(tbl.td, "whitespace-nowrap text-xs text-text-muted")} title={fmtTime(k.created_at)}>{relTime(k.created_at)}</td>
                      <td className={tbl.td}>
                        <div className="flex justify-end gap-1.5">
                          <button type="button" onClick={() => copy(k.key)} className={ctl.icon} title="Salin" aria-label={`Salin key ${k.name}`}><Copy className="size-4" /></button>
                          <button type="button" onClick={() => setDel(k)} className={cn(ctl.icon, "hover:border-danger/40 hover:text-danger")} title="Hapus" aria-label={`Hapus key ${k.name}`}><Trash2 className="size-4" /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        )}

        <Section icon={Code2} tone="info" title="Cara pakai" desc="Base URL untuk client OpenAI (SDK, Cursor, LibreChat, dll.)"
          actions={<button type="button" onClick={() => copy(`${base}/v1`, "Base URL")} className={ctl.ghost}><Copy className="size-4" /> Base URL</button>}>
          <div className="space-y-3">
            <Field label="Base URL">
              <code className="block truncate border border-border-subtle bg-surface-2 px-3 py-2 font-mono text-[13px]">{base}/v1</code>
            </Field>
            <Field label="Contoh curl">
              <pre className="overflow-x-auto border border-border-subtle bg-surface-2 p-3 font-mono text-[12px] leading-relaxed text-text-main">{curl}</pre>
            </Field>
            <Field label="Generate gambar">
              <pre className="overflow-x-auto border border-border-subtle bg-surface-2 p-3 font-mono text-[12px] leading-relaxed text-text-main">{`curl ${base}/v1/images/generations \
  -H "Authorization: Bearer ${mask(sample)}" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"kucing oranye","n":2,"size":"1024x1024"}'`}</pre>
            </Field>
            <p className="text-[11px] text-text-subtle">Chat bisa menerima gambar (format OpenAI <code className="font-mono">image_url</code>, data URL / http). Model: grok-auto (Grok 4.5, rekomendasi) & grok-fast. Alias gpt-4o / gpt-4 → auto, grok-3-mini / grok-2 → fast.</p>
          </div>
        </Section>
      </div>

      {modal && (
        <Modal title="Buat API key" desc="Beri nama supaya gampang dilacak pemakaiannya." icon={KeyRound} onClose={closeNew}
          footer={<>
            <button type="button" onClick={closeNew} className={ctl.btn}>Batal</button>
            <button type="button" onClick={create} disabled={saving} className={ctl.primary}>{saving && <Loader2 className="size-4 animate-spin" />} Buat key</button>
          </>}>
          <Field label="Nama key" hint="Contoh: Cursor laptop, Bot WA, LibreChat">
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} placeholder="Nama key" className={cn(ctl.input, "w-full")} />
          </Field>
        </Modal>
      )}

      {created && (
        <Modal title="Key berhasil dibuat" desc="Salin & simpan sekarang." icon={Check} onClose={() => setCreated(null)}
          footer={<>
            <button type="button" onClick={() => setCreated(null)} className={ctl.btn}>Tutup</button>
            <button type="button" onClick={() => copy(created)} className={ctl.primary}><Copy className="size-4" /> Salin key</button>
          </>}>
          <code className="block break-all border border-primary/30 bg-primary/5 px-3 py-2.5 font-mono text-[13px] text-text-main">{created}</code>
        </Modal>
      )}

      {del && (
        <Modal title="Hapus API key?" icon={Trash2} onClose={() => setDel(null)}
          footer={<>
            <button type="button" onClick={() => setDel(null)} className={ctl.btn}>Batal</button>
            <button type="button" onClick={() => remove(del)} disabled={deleting} className={ctl.danger}>{deleting ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />} Hapus</button>
          </>}>
          <p className="text-sm text-text-muted">
            Key <b className="font-semibold text-text-main">{del.name}</b> (<span className="font-mono">{mask(del.key)}</span>) langsung berhenti berfungsi. Aplikasi yang memakainya akan mendapat 401.
          </p>
        </Modal>
      )}
    </div>
  );
}

export default function KeysPage() {
  return <Suspense><KeysView /></Suspense>;
}

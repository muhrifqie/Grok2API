"use client";

/**
 * MODEL & KUOTA — katalog model asli dari grok.com (POST /rest/models) + kuota per akun (POST /rest/rate-limits).
 * Backend meng-cache kuota 5 mnt & katalog 10 mnt; tombol Segarkan memaksa ambil ulang.
 */
import { useState } from "react";
import { Ban, Boxes, CircleCheck, Cpu, Gauge, ImageIcon, Info, Layers, MessagesSquare, UserX, Video } from "lucide-react";
import { relTime } from "@/app/lib/api";
import { forceQuota, fmtWindow, IMAGINE_LABEL, MODEL_INFO, modelsRes, quotaRes, statsRes, useRes, type Limit } from "@/app/lib/store";
import { cn, fmt } from "@/app/lib/utils";
import { toast } from "@/app/components/toast";
import { Chip, Empty, ErrorBox, Meter, PageHead, RefreshBtn, Section, Skel, Stat, tbl, type Tone } from "@/app/components/ui";

const meterTone = (l: Limit): Tone => (l.remaining === 0 ? "danger" : l.remaining / l.total < 0.25 ? "warning" : "success");

function LimitCell({ l }: { l?: Limit }) {
  if (!l) return <span className="text-xs text-text-subtle">—</span>;
  return (
    <div className="min-w-[7rem] space-y-1">
      <div className="flex items-baseline justify-between gap-2 text-[13px]">
        <span className={cn("font-semibold tabular-nums", l.remaining === 0 && "text-danger")}>{fmt(l.remaining)}<span className="font-normal text-text-subtle">/{fmt(l.total)}</span></span>
        <span className="text-[11px] text-text-subtle">{fmtWindow(l.window)}</span>
      </div>
      <Meter value={l.remaining / l.total} tone={meterTone(l)} />
    </div>
  );
}

/** Kelompokkan model internal (plugins/imagine/companion) supaya tabel utama ringkas. */
const groupOf = (id: string) => (id.startsWith("grok-plugins") ? "Plugin Office/Browser" : id.startsWith("imagine") ? "Imagine (gambar/video)" : id.includes("companion") ? "Companion" : "Lainnya");

export default function ModelsPage() {
  const q = useRes(quotaRes);
  const cat = useRes(modelsRes);
  const st = useRes(statsRes);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    setBusy(true);
    try { await Promise.all([forceQuota(), modelsRes.refresh(), statsRes.refresh()]); toast.success("Kuota & katalog diperbarui dari grok.com"); }
    catch (e) { toast.error((e as Error).message); }
    finally { setBusy(false); }
  };

  const accs = q.data?.accounts ?? [];
  const tracked = q.data?.models ?? [];
  const sum = (m: string) => {
    const ls = accs.map((a) => a.models?.[m]).filter(Boolean) as Limit[];
    return ls.length ? { remaining: ls.reduce((s, l) => s + l.remaining, 0), total: ls.reduce((s, l) => s + l.total, 0), window: ls[0].window } : undefined;
  };
  const g3 = sum("grok-3");
  const empty = accs.filter((a) => a.models?.["grok-3"]?.remaining === 0).length;
  const perAcc = accs.find((a) => a.models?.["grok-3"])?.models?.["grok-3"];
  const models = cat.data?.models ?? [];
  const modes = q.data?.modes ?? [];
  const imagineKeys = [...new Set(accs.flatMap((a) => Object.keys(a.imagine ?? {})))];
  const imagineSum = (k: string) => accs.reduce((t, a) => t + (a.imagine?.[k]?.remaining ?? 0), 0);
  const imagineWin = (k: string) => accs.find((a) => a.imagine?.[k])?.imagine?.[k]?.window ?? 0;
  const main = models.filter((m) => !m.hidden || tracked.includes(m.id));
  const rest = models.filter((m) => !main.includes(m));
  const groups = rest.reduce<Record<string, string[]>>((o, m) => { (o[groupOf(m.id)] ??= []).push(m.id); return o; }, {});

  return (
    <div className="space-y-5">
      <PageHead icon={Cpu} eyebrow="Katalog Grok" title="Model & Kuota"
        desc="Data langsung dari grok.com memakai akun di pool. Kuota dihitung Grok per akun dalam jendela waktu tertentu — makin banyak akun valid, makin besar kapasitas API."
        actions={<RefreshBtn onClick={refresh} busy={busy || q.validating} label="Ambil ulang" />} />

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {!q.data ? Array.from({ length: 4 }, (_, i) => <Skel key={i} className="h-[92px]" />) : (
          <>
            <Stat label="Sisa kuota grok-3 (semua akun)" icon={Gauge} tone={g3 && g3.remaining / g3.total < 0.25 ? "warning" : "success"}
              value={g3 ? <>{fmt(g3.remaining)}<span className="text-base font-semibold text-text-subtle">/{fmt(g3.total)}</span></> : "—"}
              hint={g3 ? `≈ ${fmt(g3.remaining)} request lagi · jendela ${fmtWindow(g3.window)}` : "belum terbaca"} />
            <Stat label="Akun habis kuota" icon={UserX} tone="danger" dim={!empty} value={fmt(empty)} hint={empty ? "otomatis dilewati pool" : "semua masih bisa dipakai"} />
            <Stat label="Kuota per akun gratis" icon={Layers} tone="info" value={perAcc ? `${fmt(perAcc.total)}` : "—"}
              hint={perAcc ? `request / ${fmtWindow(perAcc.window)} · ${fmt(st.data?.valid ?? accs.length)} akun valid = ${fmt(perAcc.total * (st.data?.valid ?? accs.length))}` : undefined} />
            <Stat label="Mode chat didukung" icon={CircleCheck} tone="primary" value={fmt(modes.filter((m) => m.ok).length)}
              hint={`dari ${fmt(modes.length)} mode di grok.com · ${fmt(models.length)} model di katalog`} />
          </>
        )}
      </div>

      {/* Mode chat (yang dipilih di grok.com) */}
      <Section icon={MessagesSquare} title="Mode chat" desc="Pilihan mode di grok.com & cara memakainya lewat API (diuji di akun gratis)" flush>
        {!q.data ? <div className="space-y-px p-1">{Array.from({ length: 4 }, (_, i) => <Skel key={i} className="h-12" />)}</div> : (
          <div className={tbl.wrap}>
            <table className={cn(tbl.table, "min-w-[760px]")}>
              <thead>
                <tr>
                  <th className={tbl.th}>Mode</th>
                  <th className={tbl.th}>Lewat proxy</th>
                  <th className={tbl.th}>Model di API</th>
                  <th className={tbl.th}>Kuota dipakai (semua akun)</th>
                </tr>
              </thead>
              <tbody>
                {modes.map((m) => (
                  <tr key={m.mode} className={cn(tbl.row, !m.ok && "text-text-muted")}>
                    <td className={tbl.td}>
                      <p className="font-semibold text-text-main">{m.label}</p>
                      <p className="font-mono text-[11px] text-text-subtle">{m.mode}</p>
                    </td>
                    <td className={tbl.td}>{m.ok ? <Chip tone="success"><CircleCheck className="size-3" /> Jalan</Chip> : <Chip title="Gateway membalas stream_error untuk akun gratis tanpa token anti-bot browser"><Ban className="size-3" /> Ditolak</Chip>}</td>
                    <td className={tbl.td}>
                      {m.aliases.length ? <div className="flex flex-wrap gap-1">{m.aliases.map((a, i) => <Chip key={a} tone={i === 0 ? "primary" : "muted"} className="font-mono font-medium">{a}</Chip>)}</div>
                        : <span className="text-xs text-text-subtle">hanya di browser</span>}
                    </td>
                    <td className={tbl.td}>
                      <LimitCell l={sum(m.quota)} />
                      <p className="mt-0.5 font-mono text-[10px] text-text-subtle">{m.quota}</p>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* Kuota Imagine */}
      {!!imagineKeys.length && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {imagineKeys.map((k) => (
            <Stat key={k} label={`Imagine · ${IMAGINE_LABEL[k] ?? k}`} icon={k.startsWith("video") ? Video : ImageIcon} tone="info" dim={!imagineSum(k)}
              value={fmt(imagineSum(k))} hint={`sisa / ${fmtWindow(imagineWin(k))} · semua akun${k.startsWith("video") ? " · video belum didukung" : ""}`} />
          ))}
        </div>
      )}

      {/* Model utama */}
      <Section icon={Cpu} title="Model di akun gratis" desc={cat.data ? `Sumber: ${cat.data.source} · default gratis = ${cat.data.defaults.defaultFreeModel} (${cat.data.defaults.defaultFreeMode?.replace("MODEL_MODE_", "")})` : "Memuat katalog…"} flush>
        {!cat.data ? (
          cat.error ? <ErrorBox msg={cat.error} onRetry={() => modelsRes.refresh()} /> : <div className="space-y-px p-1">{Array.from({ length: 4 }, (_, i) => <Skel key={i} className="h-14" />)}</div>
        ) : (
          <div className={tbl.wrap}>
            <table className={cn(tbl.table, "min-w-[880px]")}>
              <thead>
                <tr>
                  <th className={tbl.th}>Model</th>
                  <th className={tbl.th}>Mode</th>
                  <th className={tbl.th}>Di grok.com</th>
                  <th className={tbl.th}>Sisa kuota (semua akun)</th>
                  <th className={tbl.th}>Proxy</th>
                  <th className={tbl.th}>Catatan</th>
                </tr>
              </thead>
              <tbody>
                {main.map((m) => {
                  const info = MODEL_INFO[m.id];
                  return (
                    <tr key={m.id} className={cn(tbl.row, !m.proxy && "text-text-muted")}>
                      <td className={tbl.td}>
                        <p className="font-semibold text-text-main">{info?.label ?? m.id}</p>
                        <p className="font-mono text-[11px] text-text-subtle">{m.id}</p>
                      </td>
                      <td className={tbl.td}><Chip>{m.mode.replace("MODEL_MODE_", "").replaceAll("_", " ").toLowerCase()}</Chip></td>
                      <td className={tbl.td}>
                        {!m.available ? <Chip tone="danger">Terkunci</Chip> : m.hidden ? <Chip>Tersembunyi</Chip> : <Chip tone="primary">Tampil</Chip>}
                        {m.badge && <Chip tone="warning" className="ml-1">{m.badge}</Chip>}
                      </td>
                      <td className={tbl.td}><LimitCell l={sum(m.id)} /></td>
                      <td className={tbl.td}>{m.proxy ? <Chip tone="success"><CircleCheck className="size-3" /> Didukung</Chip> : <Chip>Belum</Chip>}</td>
                      <td className={cn(tbl.td, "max-w-[20rem] text-xs text-text-muted")}>{info?.note ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {!!rest.length && (
          <details className="border-t border-border-subtle px-4 py-3 sm:px-5">
            <summary className="flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-text-muted hover:text-text-main">
              <Boxes className="size-3.5" /> {fmt(rest.length)} model internal lainnya (plugin, imagine, companion) — tidak dipakai untuk chat API
            </summary>
            <div className="mt-3 space-y-2.5">
              {Object.entries(groups).map(([g, ids]) => (
                <div key={g}>
                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-text-subtle">{g} · {ids.length}</p>
                  <div className="flex flex-wrap gap-1">{ids.map((id) => <Chip key={id} className="font-mono font-medium">{id}</Chip>)}</div>
                </div>
              ))}
            </div>
          </details>
        )}
      </Section>

      {/* Kuota per akun */}
      <Section icon={Layers} tone="success" title="Kuota per akun" desc={q.data ? `Cache backend ${fmtWindow(q.data.ttl)} · urut sisa grok-3 terkecil` : undefined} flush>
        {!q.data ? (
          q.error ? <ErrorBox msg={q.error} onRetry={() => quotaRes.refresh()} /> : <div className="space-y-px p-1">{Array.from({ length: 3 }, (_, i) => <Skel key={i} className="h-14" />)}</div>
        ) : !accs.length ? (
          <Empty emoji="🫙" title="Belum ada akun valid">Kuota hanya bisa dibaca dari akun yang cookie-nya valid.</Empty>
        ) : (
          <div className={tbl.scroll}>
            <table className={cn(tbl.table, "min-w-[900px]")}>
              <thead>
                <tr>
                  <th className={tbl.th}>Akun</th>
                  {tracked.map((m) => <th key={m} className={tbl.th}>{MODEL_INFO[m]?.label.split(" (")[0] ?? m}</th>)}
                  <th className={tbl.th}>Diperbarui</th>
                </tr>
              </thead>
              <tbody>
                {[...accs].sort((a, b) => (a.models?.["grok-3"]?.remaining ?? 1e9) - (b.models?.["grok-3"]?.remaining ?? 1e9)).map((a) => (
                  <tr key={a.id} className={tbl.row}>
                    <td className={tbl.td}>
                      <p className="truncate font-mono text-[13px] font-medium">{a.email}</p>
                      {a.error && <Chip tone="danger" title={a.error} className="mt-1">{a.error.slice(0, 32)}</Chip>}
                    </td>
                    {tracked.map((m) => <td key={m} className={tbl.td}><LimitCell l={a.models?.[m]} /></td>)}
                    <td className={cn(tbl.td, "whitespace-nowrap text-xs text-text-muted")}>{a.at ? relTime(new Date(a.at * 1000).toISOString()) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <p className="flex items-start gap-2 border border-border-subtle bg-surface-2/40 px-3 py-2.5 text-xs text-text-muted">
        <Info className="mt-px size-4 shrink-0 text-info" />
        <span>
          Lewat proxy, akun gratis bisa pakai mode <b className="text-text-main">Auto</b> (<code className="font-mono">grok-auto</code>) &amp; <b className="text-text-main">Fast</b> (<code className="font-mono">grok-fast</code>) — keduanya memotong kuota grok-3. Mode lain butuh token anti-bot browser, jadi tidak diproxy. Generate gambar (Imagine) tersedia lewat <code className="font-mono">/v1/images/generations</code>.
          Token di log &amp; respon API adalah <b className="text-text-main">estimasi</b> (±4 karakter/token) karena Grok tidak mengirim jumlah token.
          Membaca kuota memanggil grok.com memakai akunmu (risiko kecil, di-cache 5 menit).
        </span>
      </p>
    </div>
  );
}

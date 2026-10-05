"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Activity, AlertTriangle, CircleCheck, Download, Hash, ScrollText, Timer, X } from "lucide-react";
import { fmtTime, relTime } from "@/app/lib/api";
import { logsRes, useRes, type UsageLog } from "@/app/lib/store";
import { cn, fmt } from "@/app/lib/utils";
import { Chip, Empty, ErrorBox, FilterChip, PageHead, RefreshBtn, SearchBox, Section, Skel, SortTh, Stat, ctl, tbl, type SortState } from "@/app/components/ui";

type F = "" | "ok" | "err";
type SortKey = "created_at" | "account_email" | "model" | "duration_ms" | "completion_tokens";
const SLOW_MS = 15_000;

function LogsView() {
  const sp = useSearchParams();
  const res = useRes(logsRes);
  const all = useMemo(() => res.data ?? [], [res.data]);
  const [q, setQ] = useState("");
  const [f, setF] = useState<F>((sp.get("f") as F) || "");
  const [model, setModel] = useState("");
  const [sort, setSort] = useState<SortState<SortKey>>({ key: "created_at", dir: "desc" });

  useEffect(() => { setF((sp.get("f") as F) || ""); }, [sp]);

  const models = useMemo(() => [...new Set(all.map((l) => l.model))].sort(), [all]);
  const list = useMemo(() => {
    const ql = q.trim().toLowerCase();
    const r = all.filter((l) =>
      (!f || (f === "ok" ? l.success : !l.success)) && (!model || l.model === model) &&
      (!ql || `${l.account_email} ${l.model} ${l.error_msg}`.toLowerCase().includes(ql)));
    const d = sort.dir === "asc" ? 1 : -1;
    return [...r].sort((a, b) => (a[sort.key] < b[sort.key] ? -1 : a[sort.key] > b[sort.key] ? 1 : 0) * d);
  }, [all, q, f, model, sort]);

  const ok = all.filter((l) => l.success).length;
  const avg = all.length ? all.reduce((s, l) => s + l.duration_ms, 0) / all.length : 0;
  const filtered = !!q || !!f || !!model;
  const onSort = (k: SortKey) => setSort((s) => (s.key === k ? { key: k, dir: s.dir === "asc" ? "desc" : "asc" } : { key: k, dir: "desc" }));
  const reset = () => { setQ(""); setF(""); setModel(""); };

  function exportCsv() {
    const head = ["waktu_wib", "akun", "model", "durasi_ms", "token_masuk", "token_keluar", "status", "error"];
    const esc = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
    const rows = list.map((l: UsageLog) => [fmtTime(l.created_at), l.account_email, l.model, l.duration_ms, l.prompt_tokens, l.completion_tokens, l.success ? "ok" : "error", l.error_msg].map(esc).join(","));
    const url = URL.createObjectURL(new Blob([[head.join(","), ...rows].join("\n")], { type: "text/csv" }));
    Object.assign(document.createElement("a"), { href: url, download: `grok2api-logs-${Date.now()}.csv` }).click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-5">
      <PageHead
        icon={ScrollText}
        eyebrow="Monitoring"
        title="Usage Logs"
        desc="100 request terakhir ke /v1/chat/completions — akun yang melayani, model, durasi, dan status."
        actions={
          <>
            <RefreshBtn onClick={() => logsRes.refresh()} busy={res.validating} />
            <button type="button" onClick={exportCsv} disabled={!list.length} className={ctl.btn} title="Unduh log sesuai filter (CSV)"><Download className="size-4" /> CSV</button>
          </>
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        {!res.data ? Array.from({ length: 5 }, (_, i) => <Skel key={i} className="h-[92px]" />) : (
          <>
            <Stat label="Request tercatat" icon={Activity} tone="primary" value={fmt(all.length)} hint={all.length ? `sejak ${relTime(all[all.length - 1].created_at)}` : "belum ada"} />
            <Stat label="Tingkat sukses" icon={CircleCheck} tone={!all.length || ok / all.length >= 0.95 ? "success" : "warning"} value={all.length ? `${Math.round((ok / all.length) * 100)}%` : "—"} hint={`${fmt(ok)} sukses`} />
            <Stat label="Rata-rata durasi" icon={Timer} tone="info" value={all.length ? `${(avg / 1000).toFixed(1)}s` : "—"} hint={`${fmt(all.filter((l) => l.duration_ms > SLOW_MS).length)} lambat (>15s)`} />
            <Stat label="Token (estimasi)" icon={Hash} tone="info" value={fmt(all.reduce((t, l) => t + (l.prompt_tokens || 0) + (l.completion_tokens || 0), 0))}
              hint={`${fmt(all.reduce((t, l) => t + (l.prompt_tokens || 0), 0))} masuk · ${fmt(all.reduce((t, l) => t + (l.completion_tokens || 0), 0))} keluar`} />
            <Stat label="Error" icon={AlertTriangle} tone="danger" dim={all.length === ok} value={fmt(all.length - ok)} hint={all.length === ok ? "tidak ada" : "ketuk filter Error"} />
          </>
        )}
      </div>

      <div className="space-y-2.5 border border-border-subtle bg-surface p-3">
        <SearchBox value={q} onChange={setQ} placeholder="Cari akun, model, atau pesan error…" />
        <div className="flex flex-wrap items-center gap-1.5">
          <FilterChip on={!f} onClick={() => setF("")} count={all.length}>Semua</FilterChip>
          <FilterChip on={f === "ok"} onClick={() => setF(f === "ok" ? "" : "ok")} count={ok}>Sukses</FilterChip>
          <FilterChip on={f === "err"} onClick={() => setF(f === "err" ? "" : "err")} count={all.length - ok} tone="danger">Error</FilterChip>
          {models.length > 1 && <span aria-hidden className="mx-0.5 h-5 w-px bg-border-subtle" />}
          {models.length > 1 && models.map((m) => (
            <FilterChip key={m} on={model === m} onClick={() => setModel(model === m ? "" : m)} count={all.filter((l) => l.model === m).length}>{m}</FilterChip>
          ))}
        </div>
        {res.data && (
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted">
            <span>Menampilkan <b className="font-semibold tabular-nums text-text-main">{fmt(list.length)}</b> dari {fmt(all.length)} log</span>
            {filtered && <button type="button" onClick={reset} className="inline-flex items-center gap-1 font-semibold text-primary hover:underline"><X className="size-3.5" /> Reset filter</button>}
          </div>
        )}
      </div>

      {!res.data ? (
        res.error ? <Section><ErrorBox msg={res.error} onRetry={() => logsRes.refresh()} /></Section>
          : <Section flush><div className="space-y-px p-1">{Array.from({ length: 6 }, (_, i) => <Skel key={i} className="h-11" />)}</div></Section>
      ) : !list.length ? (
        <Section>
          <Empty emoji={all.length ? "🔍" : "📭"} title={all.length ? "Tidak ada yang cocok" : "Belum ada log"}
            action={all.length ? <button type="button" onClick={reset} className={ctl.btn}><X className="size-4" /> Reset filter</button> : undefined}>
            {all.length ? "Tidak ada log yang cocok dengan filter." : "Log muncul setelah ada request ke API."}
          </Empty>
        </Section>
      ) : (
        <Section flush>
          <div className={tbl.scroll}>
            <table className={tbl.table}>
              <thead>
                <tr>
                  <SortTh k="created_at" sort={sort} onSort={onSort}>Waktu (WIB)</SortTh>
                  <SortTh k="account_email" sort={sort} onSort={onSort}>Akun</SortTh>
                  <SortTh k="model" sort={sort} onSort={onSort}>Model</SortTh>
                  <SortTh k="duration_ms" sort={sort} onSort={onSort} num>Durasi</SortTh>
                  <SortTh k="completion_tokens" sort={sort} onSort={onSort} num>Token</SortTh>
                  <th className={tbl.th}>Status</th>
                </tr>
              </thead>
              <tbody>
                {list.map((l) => (
                  <tr key={l.id} className={tbl.row}>
                    <td className={cn(tbl.td, "whitespace-nowrap")}>
                      <span className="block text-[13px] tabular-nums">{fmtTime(l.created_at)}</span>
                      <span className="block text-[11px] text-text-subtle">{relTime(l.created_at)}</span>
                    </td>
                    <td className={cn(tbl.td, "font-mono text-[13px]")}>{l.account_email || "—"}</td>
                    <td className={tbl.td}><Chip tone="primary">{l.model}</Chip></td>
                    <td className={cn(tbl.td, tbl.num, l.duration_ms > SLOW_MS ? "font-semibold text-warning" : "")}>{(l.duration_ms / 1000).toFixed(2)}s</td>
                    <td className={cn(tbl.td, tbl.num, "whitespace-nowrap text-[13px]")} title="Estimasi: token masuk → token keluar">
                      {l.prompt_tokens || l.completion_tokens ? <><span className="text-text-subtle">{fmt(l.prompt_tokens)} →</span> <b className="font-semibold">{fmt(l.completion_tokens)}</b></> : <span className="text-text-subtle">—</span>}
                    </td>
                    <td className={tbl.td}>
                      {l.success ? <Chip tone="success"><CircleCheck className="size-3" /> OK</Chip>
                        : <Chip tone="danger" title={l.error_msg} className="max-w-[18rem] truncate"><AlertTriangle className="size-3 shrink-0" /> {l.error_msg || "Error"}</Chip>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

export default function LogsPage() {
  return <Suspense><LogsView /></Suspense>;
}

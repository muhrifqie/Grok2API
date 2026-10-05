"use client";

/**
 * KONSOL — terminal live yang menampilkan event backend (login massal, request API, cek cookie, dll).
 * Mengakumulasi terus; HANYA dikosongkan saat tombol Bersihkan ditekan. Polling tiap 1.5s (bisa di-pause).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, Download, Dot as DotIcon, Pause, Play, Terminal, Trash2, WrapText } from "lucide-react";
import { fetchConsole, type ConsoleEvent, type ConsoleLevel } from "@/app/lib/store";
import { cn, fmt } from "@/app/lib/utils";
import { toast } from "@/app/components/toast";
import { Dot, PageHead, pillCls } from "@/app/components/ui";

const MAX_LINES = 10_000; // ring di klien supaya memori tak jebol pada sesi panjang
const LEVELS: { v: ConsoleLevel; label: string; cls: string; dot: string }[] = [
  { v: "ok", label: "OK", cls: "text-success", dot: "bg-success" },
  { v: "info", label: "Info", cls: "text-text-muted", dot: "bg-text-subtle" },
  { v: "warn", label: "Warn", cls: "text-warning", dot: "bg-warning" },
  { v: "error", label: "Error", cls: "text-danger", dot: "bg-danger" },
];
const LVL = Object.fromEntries(LEVELS.map((l) => [l.v, l]));
const SOURCES = ["bulk", "login", "api", "check", "system"];
const timeFmt = (ts: string) => {
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "--:--:--" : d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Jakarta" });
};

export default function Console() {
  const [lines, setLines] = useState<ConsoleEvent[]>([]);
  const [paused, setPaused] = useState(false);
  const [levels, setLevels] = useState<Set<ConsoleLevel>>(new Set(["ok", "info", "warn", "error"]));
  const [source, setSource] = useState("all");
  const [q, setQ] = useState("");
  const [wrap, setWrap] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const [connected, setConnected] = useState(false);
  const lastId = useRef(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  // Polling: tambah event baru sejak lastId. Data lama tetap (hanya hilang saat Bersihkan).
  useEffect(() => {
    if (paused) return;
    let alive = true;
    const poll = async () => {
      try {
        const d = await fetchConsole(lastId.current);
        if (!alive) return;
        setConnected(true);
        if (d.events.length) {
          lastId.current = d.last_id;
          setLines((prev) => {
            const next = prev.concat(d.events);
            return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
          });
        } else {
          lastId.current = d.last_id;
        }
      } catch {
        if (alive) setConnected(false);
      }
    };
    void poll();
    const t = setInterval(poll, 1500);
    return () => { alive = false; clearInterval(t); };
  }, [paused]);

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return lines.filter((l) => levels.has(l.level) && (source === "all" || l.source === source)
      && (!ql || l.msg.toLowerCase().includes(ql) || l.source.includes(ql)));
  }, [lines, levels, source, q]);

  // Auto-scroll ke bawah bila pengguna memang di bawah.
  useEffect(() => {
    if (stick.current && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [filtered]);

  const onScroll = useCallback(() => {
    const el = boxRef.current;
    if (!el) return;
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    stick.current = bottom;
    setAtBottom(bottom);
  }, []);
  const jumpDown = () => { const el = boxRef.current; if (el) { el.scrollTop = el.scrollHeight; stick.current = true; setAtBottom(true); } };

  const toggleLevel = (v: ConsoleLevel) => setLevels((s) => { const n = new Set(s); n.has(v) ? n.delete(v) : n.add(v); return n; });
  const clear = () => { setLines([]); stick.current = true; setAtBottom(true); toast.success("Konsol dibersihkan"); };
  const download = () => {
    const txt = filtered.map((l) => `${timeFmt(l.ts)} [${l.source}/${l.level}] ${l.msg}`).join("\n");
    const url = URL.createObjectURL(new Blob([txt], { type: "text/plain" }));
    Object.assign(document.createElement("a"), { href: url, download: `grok2api-console-${Date.now()}.log` }).click();
    URL.revokeObjectURL(url);
  };

  const errCount = lines.filter((l) => l.level === "error").length;
  const btn = "inline-flex h-8 items-center gap-1.5 border border-border bg-surface px-2.5 text-xs font-semibold text-text-main transition-colors hover:border-primary/40 disabled:opacity-50";

  return (
    <div className="space-y-4">
      <PageHead
        icon={Terminal}
        eyebrow="Monitoring live"
        title="Konsol"
        desc="Event backend real-time: login massal, request API, cek cookie. Terus terkumpul — hanya kosong saat kamu tekan Bersihkan."
        actions={
          <div className="flex items-center gap-1.5">
            <span role="status" title={connected && !paused ? "Terhubung" : paused ? "Dijeda" : "Terputus"}
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-border-subtle bg-surface px-2.5 text-xs font-semibold text-text-muted">
              <Dot tone={paused ? "warning" : connected ? "success" : "muted"} pulse={connected && !paused} />
              {paused ? "Jeda" : connected ? "Live" : "…"}
            </span>
            <button type="button" onClick={() => setPaused((p) => !p)} className={btn}>
              {paused ? <><Play className="size-3.5" /> Lanjut</> : <><Pause className="size-3.5" /> Jeda</>}
            </button>
            <button type="button" onClick={download} disabled={!filtered.length} className={btn}><Download className="size-3.5" /> Unduh</button>
            <button type="button" onClick={clear} disabled={!lines.length} className={cn(btn, "hover:border-danger/40 hover:text-danger")}><Trash2 className="size-3.5" /> Bersihkan</button>
          </div>
        }
      />

      {/* Toolbar filter */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1.5">
          {LEVELS.map((l) => (
            <button key={l.v} type="button" onClick={() => toggleLevel(l.v)} className={pillCls(levels.has(l.v))}>
              <span className={cn("size-2 rounded-full", l.dot)} /> {l.label}
            </button>
          ))}
        </div>
        <span aria-hidden className="h-5 w-px bg-border-subtle" />
        <select value={source} onChange={(e) => setSource(e.target.value)} className="h-8 border border-border bg-surface px-2 text-xs outline-none focus:border-primary/50">
          <option value="all">semua sumber</option>
          {SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="grep…" className="h-8 min-w-0 flex-1 border border-border bg-surface px-2.5 text-xs outline-none placeholder:text-text-subtle focus:border-primary/50 sm:max-w-xs" />
        <button type="button" onClick={() => setWrap((w) => !w)} title="Bungkus baris" className={cn(btn, wrap && "border-primary/50 text-primary")}><WrapText className="size-3.5" /></button>
      </div>

      {/* Terminal */}
      <div className="relative border border-border-subtle bg-bg">
        <div className="flex items-center gap-2 border-b border-border-subtle bg-surface-2 px-3 py-1.5 text-[11px] text-text-subtle">
          <span className="flex gap-1"><span className="size-2.5 rounded-full bg-danger/70" /><span className="size-2.5 rounded-full bg-warning/70" /><span className="size-2.5 rounded-full bg-success/70" /></span>
          <span className="font-mono">grok2api — console</span>
          <span className="ml-auto tabular-nums">{fmt(filtered.length)}/{fmt(lines.length)} baris{errCount ? ` · ${fmt(errCount)} error` : ""}</span>
        </div>
        <div ref={boxRef} onScroll={onScroll} className="h-[calc(100dvh-20rem)] min-h-[20rem] overflow-auto bg-bg p-3 font-mono text-[12.5px] leading-relaxed">
          {!filtered.length ? (
            <div className="flex h-full items-center justify-center text-text-subtle">
              {lines.length ? "Tidak ada baris cocok filter." : <span className="flex items-center gap-2"><DotIcon className="size-4 animate-pulse" /> Menunggu event… coba jalankan login massal atau kirim request API.</span>}
            </div>
          ) : filtered.map((l) => (
            <div key={l.id} className={cn("flex gap-2", wrap ? "" : "whitespace-nowrap")}>
              <span className="shrink-0 text-text-subtle tabular-nums">{timeFmt(l.ts)}</span>
              <span className="shrink-0 font-semibold uppercase text-text-subtle">[{l.source}]</span>
              <span className={cn(LVL[l.level]?.cls, wrap ? "min-w-0 break-words [overflow-wrap:anywhere]" : "")}>{l.msg}</span>
            </div>
          ))}
        </div>
        {!atBottom && (
          <button type="button" onClick={jumpDown} className="absolute bottom-3 right-3 inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 text-xs font-semibold shadow-[var(--shadow-elevated)] hover:border-primary/40">
            <ArrowDown className="size-3.5" /> Ke bawah
          </button>
        )}
      </div>
      <p className="text-[11px] text-text-subtle">Maks {fmt(MAX_LINES)} baris tersimpan di tab ini (yang terlama dibuang otomatis). Jeda menghentikan polling tanpa menghapus isi.</p>
    </div>
  );
}

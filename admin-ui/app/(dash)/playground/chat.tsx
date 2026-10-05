"use client";

/**
 * Tab CHAT — percakapan multi-turn lewat /v1/chat/completions (stream). Riwayat dikirim ulang tiap giliran
 * (Grok proxy = stateless). Gambar terlampir dikirim sebagai OpenAI `image_url` (data URL) → backend upload ke Grok.
 */
import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle, Bot, Brain, ChevronDown, Copy, Hash, ImagePlus, Loader2, RotateCcw, Send, Settings2, Square, Timer, Trash2, User, X,
} from "lucide-react";
import { statsRes, useRes } from "@/app/lib/store";
import { cn, fmt } from "@/app/lib/utils";
import { toast } from "@/app/components/toast";
import { Markdown } from "@/app/components/markdown";
import { Chip, Field, IconTile, Section, ctl } from "@/app/components/ui";
import { toDataUrl } from "./image";

type Msg = {
  id: string; role: "user" | "assistant"; text: string; images?: string[]; reasoning?: string;
  status?: "streaming" | "done" | "error"; error?: string; ms?: number; firstMs?: number; model?: string;
  usage?: { prompt_tokens: number; completion_tokens: number };
};
const uid = () => Math.random().toString(36).slice(2, 10);
const SUGGEST = [
  "Jelaskan cara kerja reverse proxy dengan bahasa sederhana",
  "Buat fungsi Python untuk validasi nomor HP Indonesia",
  "Buat tabel perbandingan 3 marketplace di Indonesia",
  "Tulis caption jualan singkat untuk produk kopi susu",
];
const MAX_IMAGES = 4;

export function ChatTab({ keyVal, models, pending, onPendingUsed }: {
  keyVal: string; models: string[]; pending: string[]; onPendingUsed: () => void;
}) {
  const st = useRes(statsRes);
  const [model, setModel] = useState("grok-auto");
  const [system, setSystem] = useState("");
  const [withHistory, setWithHistory] = useState(true);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [atts, setAtts] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const stickRef = useRef(true);

  // Gambar dari tab Imagine ("Pakai di chat").
  useEffect(() => {
    if (!pending.length) return;
    setAtts((a) => [...a, ...pending].slice(0, MAX_IMAGES));
    onPendingUsed();
    taRef.current?.focus();
  }, [pending, onPendingUsed]);

  // Auto-scroll hanya kalau pengguna memang di bawah (tidak mengganggu saat membaca ke atas).
  useEffect(() => {
    const el = listRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [msgs]);

  // Textarea tumbuh mengikuti isi (maks ±8 baris).
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  async function addFiles(files: FileList | File[]) {
    const imgs = [...files].filter((f) => f.type.startsWith("image/"));
    if (!imgs.length) return;
    if (atts.length + imgs.length > MAX_IMAGES) toast.info(`Maksimal ${MAX_IMAGES} gambar per pesan`);
    try {
      const urls = await Promise.all(imgs.slice(0, MAX_IMAGES - atts.length).map((f) => toDataUrl(f)));
      setAtts((a) => [...a, ...urls]);
    } catch (e) { toast.error((e as Error).message); }
  }

  const patch = (id: string, p: Partial<Msg> | ((m: Msg) => Partial<Msg>)) =>
    setMsgs((ms) => ms.map((m) => (m.id === id ? { ...m, ...(typeof p === "function" ? p(m) : p) } : m)));

  function toOpenAI(history: Msg[]) {
    const out: unknown[] = system.trim() ? [{ role: "system", content: system }] : [];
    const turns = withHistory ? history : history.slice(-1);
    const last = turns[turns.length - 1];
    for (const m of turns) {
      if (m.role === "assistant") { if (m.status !== "error" && m.text) out.push({ role: "assistant", content: m.text }); continue; }
      // ponytail: gambar hanya dikirim di giliran terakhir (hindari upload ulang tiap giliran); giliran lama cukup ditandai.
      if (m.images?.length && m === last) {
        out.push({ role: "user", content: [{ type: "text", text: m.text }, ...m.images.map((url) => ({ type: "image_url", image_url: { url } }))] });
      } else {
        out.push({ role: "user", content: m.images?.length ? `${m.text}
[${m.images.length} gambar dilampirkan sebelumnya]` : m.text });
      }
    }
    return out;
  }

  async function run(history: Msg[]) {
    if (!keyVal) return toast.error("Belum ada API key aktif — buat dulu di menu API Keys");
    const a: Msg = { id: uid(), role: "assistant", text: "", status: "streaming", model };
    setMsgs([...history, a]);
    stickRef.current = true;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy(true);
    const t0 = performance.now();
    let first = false;
    try {
      const res = await fetch("/v1/chat/completions", {
        method: "POST", signal: ctrl.signal,
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${keyVal}` },
        body: JSON.stringify({ model, messages: toOpenAI(history), stream: true }),
      });
      if (!res.ok || !res.body) { const j = await res.json().catch(() => null); throw new Error(j?.detail || `HTTP ${res.status}`); }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "", errText = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          const t = line.trim();
          if (!t.startsWith("data:")) continue;
          const p = t.slice(5).trim();
          if (p === "[DONE]") continue;
          try {
            const j = JSON.parse(p);
            if (j.usage) patch(a.id, { usage: j.usage });
            const d = j.choices?.[0]?.delta ?? {};
            if (!first && (d.content || d.reasoning_content)) { first = true; patch(a.id, { firstMs: Math.round(performance.now() - t0) }); }
            if (d.content) {
              // Backend menyisipkan "[Error: …]" di stream saat Grok gagal → tampilkan sebagai galat, bukan isi.
              const m = /^\n?\[Error: ([\s\S]*)\]$/.exec(d.content);
              if (m) errText = m[1];
              else patch(a.id, (x) => ({ text: x.text + d.content }));
            }
            if (d.reasoning_content) patch(a.id, (x) => ({ reasoning: (x.reasoning ?? "") + d.reasoning_content }));
          } catch { /* chunk terpotong */ }
        }
      }
      const ms = Math.round(performance.now() - t0);
      patch(a.id, errText ? { status: "error", error: errText, ms } : { status: "done", ms });
      void statsRes.refresh();
    } catch (e) {
      const ms = Math.round(performance.now() - t0);
      if ((e as Error).name === "AbortError") patch(a.id, (x) => ({ status: x.text ? "done" : "error", error: x.text ? undefined : "Dihentikan", ms }));
      else patch(a.id, { status: "error", error: (e as Error).message, ms });
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  function send(text = input) {
    if (busy || (!text.trim() && !atts.length)) return;
    const u: Msg = { id: uid(), role: "user", text: text.trim() || "Jelaskan gambar ini.", images: atts.length ? atts : undefined };
    setInput("");
    setAtts([]);
    void run([...msgs, u]);
  }

  function regenerate() {
    const lastUser = msgs.map((m) => m.role).lastIndexOf("user");
    if (lastUser < 0 || busy) return;
    void run(msgs.slice(0, lastUser + 1));
  }

  async function copy(t: string) {
    try { await navigator.clipboard.writeText(t); toast.success("Disalin"); } catch { toast.error("Gagal menyalin"); }
  }

  const lastAssistant = [...msgs].reverse().find((m) => m.role === "assistant");
  const q = st.data;

  const settings = (
    <div className="space-y-3">
      <Field label="Model" hint="grok-auto = Grok 4.5 (Auto), grok-fast = Fast. Alias lain diarahkan ke salah satunya.">
        <select value={model} onChange={(e) => setModel(e.target.value)} className={cn(ctl.input, "w-full")}>
          {models.filter((m) => m !== "grok-imagine").map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </Field>
      <Field label="System prompt" hint="Instruksi peran/gaya — opsional.">
        <textarea value={system} onChange={(e) => setSystem(e.target.value)} rows={4} placeholder="Contoh: Jawab singkat dalam bahasa Indonesia santai."
          className={cn(ctl.input, "h-auto w-full resize-y py-2")} />
      </Field>
      <label className="flex cursor-pointer items-start gap-2.5 text-sm">
        <input type="checkbox" checked={withHistory} onChange={(e) => setWithHistory(e.target.checked)} className="mt-0.5 size-4 accent-[var(--primary)]" />
        <span><span className="font-semibold text-text-main">Kirim riwayat percakapan</span>
          <span className="block text-xs text-text-muted">Grok ingat konteks sebelumnya. Matikan untuk hemat token.</span></span>
      </label>
      {q?.quota_total ? (
        <p className="border border-border-subtle bg-surface-2/40 px-3 py-2 text-xs text-text-muted">
          Sisa kuota chat: <b className="tabular-nums text-text-main">{fmt(q.quota_remaining)}</b>/{fmt(q.quota_total)} request (semua akun, 24 jam).
        </p>
      ) : null}
      <button type="button" onClick={() => { abortRef.current?.abort(); setMsgs([]); }} disabled={!msgs.length} className={cn(ctl.btn, "w-full")}>
        <Trash2 className="size-4" /> Percakapan baru
      </button>
    </div>
  );

  return (
    <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <Section flush className="flex h-[calc(100dvh-15.5rem)] min-h-[28rem] flex-col sm:h-[calc(100dvh-14rem)] [&>div:last-child]:flex [&>div:last-child]:min-h-0 [&>div:last-child]:flex-1 [&>div:last-child]:flex-col">
        {/* Daftar pesan */}
        <div ref={listRef} onScroll={(e) => { const el = e.currentTarget; stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}
          className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
          {!msgs.length ? (
            <div className="mx-auto flex h-full max-w-xl flex-col items-center justify-center gap-4 text-center">
              <IconTile icon={Bot} size="lg" />
              <div>
                <p className="text-lg font-bold tracking-tight">Mulai ngobrol dengan Grok</p>
                <p className="mt-1 text-sm text-text-muted">Tulis pesan, tempel atau seret gambar untuk dianalisis.</p>
              </div>
              <div className="grid w-full gap-2 sm:grid-cols-2">
                {SUGGEST.map((s) => (
                  <button key={s} type="button" onClick={() => send(s)} className="border border-border-subtle bg-surface px-3 py-2.5 text-left text-[13px] text-text-muted transition-colors hover:border-primary/40 hover:text-text-main">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <ul className="mx-auto max-w-3xl space-y-6">
              {msgs.map((m) => m.role === "user" ? (
                <li key={m.id} className="flex justify-end gap-3">
                  <div className="max-w-[85%] space-y-2">
                    {m.images && (
                      <div className="flex flex-wrap justify-end gap-1.5">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        {m.images.map((src, i) => <img key={i} src={src} alt="lampiran" className="size-28 border border-border-subtle object-cover" />)}
                      </div>
                    )}
                    {m.text && <div className="whitespace-pre-wrap bg-surface-2 px-3.5 py-2.5 text-sm leading-relaxed">{m.text}</div>}
                  </div>
                  <span aria-hidden className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-text-muted"><User className="size-4" /></span>
                </li>
              ) : (
                <li key={m.id} className="flex gap-3">
                  <span aria-hidden className="mt-0.5 grid size-7 shrink-0 place-items-center bg-primary text-primary-foreground"><Bot className="size-4" /></span>
                  <div className="min-w-0 flex-1 space-y-2">
                    {m.reasoning && (
                      <details className="border border-border-subtle px-3 py-2 text-xs" open={m.status === "streaming" && !m.text}>
                        <summary className="flex cursor-pointer items-center gap-1.5 font-semibold text-text-muted"><Brain className="size-3.5" /> Proses berpikir</summary>
                        <p className="mt-2 whitespace-pre-wrap leading-relaxed text-text-muted">{m.reasoning}</p>
                      </details>
                    )}
                    {m.text ? (
                      <div className={cn(m.status === "streaming" && "streaming")}><Markdown text={m.text} /></div>
                    ) : m.status === "streaming" ? (
                      <p className="flex items-center gap-2 py-1 text-sm text-text-muted"><Loader2 className="size-4 animate-spin" /> Grok sedang berpikir…</p>
                    ) : null}
                    {m.status === "error" && (
                      <div role="alert" className="flex flex-wrap items-center gap-2 border border-danger/30 bg-danger/5 px-3 py-2 text-xs text-danger">
                        <AlertTriangle className="size-3.5 shrink-0" /> <span className="min-w-0 flex-1">{m.error}</span>
                        {m === lastAssistant && <button type="button" onClick={regenerate} className="font-semibold underline-offset-2 hover:underline">Coba lagi</button>}
                      </div>
                    )}
                    {m.status !== "streaming" && (
                      <div className="flex flex-wrap items-center gap-1.5 text-text-subtle">
                        {m.model && <Chip>{m.model}</Chip>}
                        {m.firstMs != null && <Chip title="Waktu sampai token pertama"><Timer className="size-3" /> {(m.firstMs / 1000).toFixed(1)}s</Chip>}
                        {m.ms != null && <Chip title="Total durasi">{(m.ms / 1000).toFixed(1)}s total</Chip>}
                        {m.usage && <Chip title="Estimasi ±4 karakter/token — Grok tidak mengirim jumlah token"><Hash className="size-3" /> ~{fmt(m.usage.prompt_tokens + m.usage.completion_tokens)} token</Chip>}
                        <span className="flex-1" />
                        {m.text && <button type="button" onClick={() => copy(m.text)} className={cn(ctl.ghost, "h-7 px-2 text-xs")}><Copy className="size-3.5" /> Salin</button>}
                        {m === lastAssistant && !busy && <button type="button" onClick={regenerate} className={cn(ctl.ghost, "h-7 px-2 text-xs")}><RotateCcw className="size-3.5" /> Ulangi</button>}
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Composer */}
        <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); void addFiles(e.dataTransfer.files); }}
          className={cn("border-t border-border-subtle p-3 transition-colors sm:p-4", drag && "bg-primary/5")}>
          <div className="mx-auto max-w-3xl">
            {!!atts.length && (
              <div className="mb-2 flex flex-wrap gap-2">
                {atts.map((src, i) => (
                  <div key={i} className="relative">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={src} alt="" className="size-16 border border-border-subtle object-cover" />
                    <button type="button" onClick={() => setAtts((a) => a.filter((_, j) => j !== i))} aria-label="Hapus gambar"
                      className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full bg-text-main text-bg"><X className="size-3" /></button>
                  </div>
                ))}
              </div>
            )}
            <div className="flex items-end gap-2 border border-border bg-surface px-2 py-1.5 focus-within:border-primary/50">
              <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={(e) => { if (e.target.files) void addFiles(e.target.files); e.target.value = ""; }} />
              <button type="button" onClick={() => fileRef.current?.click()} disabled={atts.length >= MAX_IMAGES} title="Lampirkan gambar" aria-label="Lampirkan gambar"
                className="grid size-9 shrink-0 place-items-center text-text-muted transition-colors hover:text-text-main disabled:opacity-40"><ImagePlus className="size-5" /></button>
              <textarea ref={taRef} value={input} rows={1} onChange={(e) => setInput(e.target.value)}
                onPaste={(e) => { const f = [...e.clipboardData.files]; if (f.length) { e.preventDefault(); void addFiles(f); } }}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
                placeholder={drag ? "Lepas gambar di sini…" : "Tulis pesan untuk Grok…"}
                className="max-h-[200px] min-h-9 flex-1 resize-none bg-transparent py-2 text-sm outline-none placeholder:text-text-subtle" />
              {busy ? (
                <button type="button" onClick={() => abortRef.current?.abort()} className={cn(ctl.btn, "shrink-0")} title="Hentikan"><Square className="size-4" /> Stop</button>
              ) : (
                <button type="button" onClick={() => send()} disabled={!input.trim() && !atts.length} className={cn(ctl.primary, "shrink-0")} title="Kirim (Enter)"><Send className="size-4" /> Kirim</button>
              )}
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-2 text-[11px] text-text-subtle">
              <span>Enter kirim · Shift+Enter baris baru · tempel / seret gambar (maks {MAX_IMAGES})</span>
              <button type="button" onClick={() => setShowSettings((v) => !v)} className="inline-flex items-center gap-1 font-semibold hover:text-text-main lg:hidden">
                <Settings2 className="size-3.5" /> Pengaturan <ChevronDown className={cn("size-3 transition-transform", showSettings && "rotate-180")} />
              </button>
            </div>
          </div>
        </div>
      </Section>

      {/* Pengaturan: kolom kanan di desktop, lipat di HP */}
      <Section icon={Settings2} title="Pengaturan" className={cn("lg:sticky lg:top-20", !showSettings && "hidden lg:block")}>
        {settings}
      </Section>
    </div>
  );
}

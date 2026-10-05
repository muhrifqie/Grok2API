"use client";

/** Tab IMAGINE — generate gambar lewat /v1/images/generations (Grok Imagine). Hasil disimpan backend di /v1/media/<id>.jpg. */
import { useEffect, useState } from "react";
import { AlertTriangle, Download, ExternalLink, ImageIcon, Loader2, MessageSquarePlus, Sparkles, Wand2 } from "lucide-react";
import { quotaRes, useRes } from "@/app/lib/store";
import { cn, fmt } from "@/app/lib/utils";
import { toast } from "@/app/components/toast";
import { Chip, Empty, Field, Section, ctl, pillCls } from "@/app/components/ui";
import { toDataUrl, viaProxy } from "./image";

type Img = { url: string; id: string; width: number; height: number; moderated?: boolean };
type Batch = { id: string; prompt: string; revised?: string; aspect: string; pro: boolean; n: number; status: "loading" | "done" | "error"; error?: string; ms?: number; images: Img[]; t0: number };

const ASPECTS: { v: string; w: number; h: number }[] = [
  { v: "1:1", w: 1, h: 1 }, { v: "2:3", w: 2, h: 3 }, { v: "3:2", w: 3, h: 2 }, { v: "9:16", w: 9, h: 16 }, { v: "16:9", w: 16, h: 9 },
];
const IDEAS = ["Kucing oranye tidur di jendela, cahaya pagi, gaya foto", "Logo minimalis kedai kopi, garis hitam putih", "Pemandangan sawah Bali saat senja, sinematik", "Ilustrasi flat karakter kurir motor, warna cerah"];

function Elapsed({ t0 }: { t0: number }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 500); return () => clearInterval(t); }, []);
  return <>{((Date.now() - t0) / 1000).toFixed(1)}s</>;
}

export function ImagineTab({ keyVal, onUseInChat }: { keyVal: string; onUseInChat: (dataUrls: string[]) => void }) {
  const quota = useRes(quotaRes);
  const [prompt, setPrompt] = useState("");
  const [aspect, setAspect] = useState("1:1");
  const [n, setN] = useState(2);
  const [pro, setPro] = useState(false);
  const [batches, setBatches] = useState<Batch[]>([]);
  const busy = batches.some((b) => b.status === "loading");
  const proLeft = (quota.data?.accounts ?? []).reduce((t, a) => t + (a.imagine?.imagePro?.remaining ?? 0), 0);

  async function generate() {
    if (!prompt.trim() || busy) return;
    if (!keyVal) return toast.error("Belum ada API key aktif — buat dulu di menu API Keys");
    const b: Batch = { id: Math.random().toString(36).slice(2), prompt: prompt.trim(), aspect, pro, n, status: "loading", images: [], t0: Date.now() };
    setBatches((x) => [b, ...x]);
    const set = (p: Partial<Batch>) => setBatches((x) => x.map((y) => (y.id === b.id ? { ...y, ...p } : y)));
    try {
      const res = await fetch("/v1/images/generations", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${keyVal}` },
        body: JSON.stringify({ prompt: b.prompt, n, aspect_ratio: aspect, quality: pro ? "hd" : "standard", response_format: "url" }),
      });
      const j = await res.json().catch(() => null);
      if (!res.ok) throw new Error(j?.detail || `HTTP ${res.status}`);
      const images: Img[] = j.data.map((d: { url: string; x_grok: Img }) => ({ ...d.x_grok, url: viaProxy(d.url) }));
      set({ status: "done", images, revised: j.data[0]?.revised_prompt, ms: Date.now() - b.t0 });
      if (pro) void quotaRes.refresh();
    } catch (e) {
      set({ status: "error", error: (e as Error).message, ms: Date.now() - b.t0 });
    }
  }

  async function useInChat(urls: string[]) {
    try {
      const data = await Promise.all(urls.map(async (u) => toDataUrl(await (await fetch(u)).blob())));
      onUseInChat(data);
      toast.success(`${urls.length} gambar ditambahkan ke Chat`);
    } catch { toast.error("Gagal memuat gambar"); }
  }

  return (
    <div className="grid items-start gap-5 lg:grid-cols-[22rem_minmax(0,1fr)]">
      <Section icon={Wand2} title="Buat gambar" desc="Grok Imagine · Enter untuk generate" className="lg:sticky lg:top-20">
        <div className="space-y-4">
          <Field label="Prompt">
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={5} placeholder="Deskripsikan gambar yang diinginkan…"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void generate(); } }}
              className={cn(ctl.input, "h-auto w-full resize-y py-2")} />
          </Field>
          <div>
            <p className="mb-1.5 text-xs font-semibold text-text-main">Rasio</p>
            <div className="flex flex-wrap gap-1.5">
              {ASPECTS.map((a) => (
                <button key={a.v} type="button" aria-pressed={aspect === a.v} onClick={() => setAspect(a.v)} className={pillCls(aspect === a.v)}>
                  <span aria-hidden className="inline-block border-[1.5px] border-current" style={{ width: a.w >= a.h ? 14 : (14 * a.w) / a.h, height: a.h >= a.w ? 14 : (14 * a.h) / a.w }} />
                  {a.v}
                </button>
              ))}
            </div>
          </div>
          <div>
            <p className="mb-1.5 text-xs font-semibold text-text-main">Jumlah gambar</p>
            <div className="flex gap-1.5">
              {[1, 2, 3, 4].map((k) => <button key={k} type="button" aria-pressed={n === k} onClick={() => setN(k)} className={cn(pillCls(n === k), "w-11 justify-center")}>{k}</button>)}
            </div>
          </div>
          <label className="flex cursor-pointer items-start gap-2.5 border border-border-subtle p-3 text-sm">
            <input type="checkbox" checked={pro} onChange={(e) => setPro(e.target.checked)} className="mt-0.5 size-4 accent-[var(--primary)]" />
            <span className="min-w-0">
              <span className="flex items-center gap-1.5 font-semibold text-text-main"><Sparkles className="size-3.5" /> Imagine Pro</span>
              <span className="block text-xs text-text-muted">Kualitas lebih tinggi, lebih lambat. Kuota terbatas: sisa <b className="tabular-nums text-text-main">{quota.data ? fmt(proLeft) : "…"}</b> (semua akun, 24 jam).</span>
            </span>
          </label>
          <button type="button" onClick={generate} disabled={!prompt.trim() || busy} className={cn(ctl.primary, "h-10 w-full")}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Wand2 className="size-4" />} {busy ? "Membuat…" : `Generate ${n} gambar`}
          </button>
          {!batches.length && (
            <div className="space-y-1.5">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-text-subtle">Ide prompt</p>
              {IDEAS.map((i) => <button key={i} type="button" onClick={() => setPrompt(i)} className="block w-full truncate text-left text-xs text-text-muted hover:text-text-main">→ {i}</button>)}
            </div>
          )}
        </div>
      </Section>

      <div className="min-w-0 space-y-5">
        {!batches.length ? (
          <Section><Empty icon={ImageIcon} title="Belum ada gambar">Hasil generate muncul di sini — bisa diunduh atau langsung dipakai di Chat untuk dianalisis.</Empty></Section>
        ) : batches.map((b) => (
          <Section key={b.id} icon={b.status === "error" ? AlertTriangle : ImageIcon} tone={b.status === "error" ? "danger" : "primary"}
            title={<span className="line-clamp-2">{b.prompt}</span>}
            desc={<span className="flex flex-wrap items-center gap-1.5 pt-0.5">
              <Chip>{b.aspect}</Chip><Chip>{b.n} gambar</Chip>{b.pro && <Chip tone="warning"><Sparkles className="size-3" /> Pro</Chip>}
              {b.status === "loading" ? <Chip tone="info"><Loader2 className="size-3 animate-spin" /> <Elapsed t0={b.t0} /></Chip> : b.ms != null && <Chip>{(b.ms / 1000).toFixed(1)}s</Chip>}
            </span>}
            actions={b.status === "done" && b.images.length > 0 ? <button type="button" onClick={() => useInChat(b.images.map((i) => i.url))} className={ctl.ghost}><MessageSquarePlus className="size-4" /> Pakai di chat</button> : undefined}>
            {b.status === "error" ? (
              <p role="alert" className="text-sm text-danger">{b.error}</p>
            ) : (
              <div className={cn("grid gap-3", b.n === 1 ? "grid-cols-1 sm:max-w-md" : "grid-cols-2")}>
                {(b.status === "loading" ? Array.from({ length: b.n }, () => null) : b.images).map((im, i) => {
                  const ar = ASPECTS.find((a) => a.v === b.aspect) ?? ASPECTS[0];
                  return (
                    <figure key={im?.id ?? i} className="group relative overflow-hidden border border-border-subtle bg-surface-2" style={{ aspectRatio: `${ar.w} / ${ar.h}` }}>
                      {im ? (
                        <>
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={im.url} alt={b.prompt} loading="lazy" className="size-full object-cover" />
                          {im.moderated && <Chip tone="warning" className="absolute left-2 top-2">Dimoderasi</Chip>}
                          <figcaption className="absolute inset-x-0 bottom-0 flex items-center justify-end gap-1 bg-gradient-to-t from-black/70 to-transparent p-2 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
                            <span className="mr-auto text-[11px] font-medium text-white/80">{im.width}×{im.height}</span>
                            <a href={im.url} target="_blank" rel="noopener noreferrer" title="Buka ukuran penuh" className="grid size-8 place-items-center bg-black/50 text-white hover:bg-black/70"><ExternalLink className="size-4" /></a>
                            <a href={im.url} download={`grok-imagine-${im.id}.jpg`} title="Unduh" className="grid size-8 place-items-center bg-black/50 text-white hover:bg-black/70"><Download className="size-4" /></a>
                            <button type="button" onClick={() => useInChat([im.url])} title="Pakai di chat" className="grid size-8 place-items-center bg-black/50 text-white hover:bg-black/70"><MessageSquarePlus className="size-4" /></button>
                          </figcaption>
                        </>
                      ) : <div className="sb-shimmer size-full" />}
                    </figure>
                  );
                })}
              </div>
            )}
          </Section>
        ))}
      </div>
    </div>
  );
}

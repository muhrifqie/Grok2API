"use client";

/**
 * BERANDA panel admin — pola sama dgn Beranda Waroengkuy Admin: sapaan WIB + tanggal, ringkasan HARI INI, "Perlu tindakan"
 * (tile berangka yg bisa diketuk ke halamannya, 0 = diredupkan), lalu request terakhir + pemakaian akun (kiri) & pintasan (kanan).
 */
import Link from "next/link";
import { useSyncExternalStore } from "react";
import {
  Activity, AlertTriangle, Bell, CalendarDays, ChevronRight, CircleCheck, Gauge, Hash, Info, KeyRound, Layers, LayoutDashboard, Loader, LogIn,
  Moon, ScrollText, Sparkles, Sun, Sunrise, Sunset, Timer, Upload, UserPlus, UserX, Zap, type LucideIcon,
} from "lucide-react";
import { logsRes, poolRes, quotaRes, statsRes, useRes } from "@/app/lib/store";
import { relTime } from "@/app/lib/api";
import { fmt } from "@/app/lib/utils";
import { Chip, Empty, ErrorBox, GroupTitle, IconTile, Meter, PageHead, RefreshBtn, Section, Skel, Stat, ctl, type Tone } from "@/app/components/ui";

// Jam dinding WIB (klien saja → tanpa hydration mismatch), diperbarui tiap menit.
const subMinute = (cb: () => void) => { const t = window.setInterval(cb, 60_000); return () => window.clearInterval(t); };
function clockSnap(): string {
  const d = new Date();
  const h = Number(new Intl.DateTimeFormat("en-GB", { hour: "numeric", hourCycle: "h23", timeZone: "Asia/Jakarta" }).format(d));
  const part = h < 11 ? "pagi" : h < 15 ? "siang" : h < 18 ? "sore" : "malam";
  const date = d.toLocaleDateString("id-ID", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "Asia/Jakarta" });
  return `${part}|${date}`;
}
const PART_ICON: Record<string, LucideIcon> = { pagi: Sunrise, siang: Sun, sore: Sunset, malam: Moon };

function Shortcut({ href, icon, tone = "primary", title, desc, value }: { href: string; icon: LucideIcon; tone?: Tone; title: string; desc: string; value?: React.ReactNode }) {
  return (
    <Link href={href} className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-2/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary sm:px-5">
      <IconTile icon={icon} tone={tone} size="sm" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-semibold text-text-main group-hover:text-primary">{title}</span>
        <span className="block truncate text-xs text-text-muted">{desc}</span>
      </span>
      {value != null && <span className="shrink-0 text-sm font-bold tabular-nums text-text-main">{value}</span>}
      <ChevronRight className="size-4 shrink-0 text-text-subtle transition-transform group-hover:translate-x-0.5" />
    </Link>
  );
}

export default function Home() {
  const clock = useSyncExternalStore(subMinute, clockSnap, () => "");
  const [part, dateLabel] = clock ? clock.split("|") : ["", ""];
  const st = useRes(statsRes);
  const pool = useRes(poolRes);
  const lg = useRes(logsRes);
  const qa = useRes(quotaRes).data?.accounts ?? [];
  const s = st.data;
  const accs = pool.data?.rows ?? [];  // pool = akun valid (maks 50), bukan seluruh 8k
  const busy = st.validating || pool.validating || lg.validating;
  const refresh = () => { void statsRes.refresh(); void poolRes.refresh(); void logsRes.refresh(); };

  const invalid = s?.invalid ?? 0;
  const failed = s?.failed ?? 0;
  const loggingIn = s?.login_busy ?? 0;
  const errToday = s?.errors_today ?? 0;
  const total = invalid + errToday;
  const rate = s && s.today ? s.ok_today / s.today : null;

  const summary = !s
    ? st.error ? "Backend tidak merespons — cek server.py." : "Memuat ringkasan…"
    : total ? `${fmt(total)} hal perlu perhatian${loggingIn ? ` · ${fmt(loggingIn)} akun sedang login` : ""}.`
    : `Semua akun sehat, ${fmt(s.pool_size)} akun aktif di pool. API berjalan lancar ✨`;

  const sk = (w: string) => <Skel className={`mt-1 h-7 ${w}`} />;
  const tiles: { label: string; n: number; href: string; icon: LucideIcon; tone: Tone }[] = [
    { label: "Akun invalid", n: invalid, href: "/accounts?f=invalid", icon: UserX, tone: "danger" },
    { label: "Login gagal", n: failed, href: "/accounts?f=failed", icon: AlertTriangle, tone: "danger" },
    { label: "Sedang login", n: loggingIn, href: "/accounts?f=busy", icon: Loader, tone: "info" },
    { label: "Error hari ini", n: errToday, href: "/logs?f=err", icon: Bell, tone: "warning" },
  ];

  return (
    <div className="space-y-6">
      <PageHead
        icon={PART_ICON[part] ?? LayoutDashboard}
        eyebrow={dateLabel || "Panel admin"}
        title={<>{part ? `Selamat ${part}, Admin` : "Halo, Admin"} <span aria-hidden>👋</span></>}
        desc={summary}
        actions={
          <>
            <RefreshBtn onClick={refresh} busy={busy} />
            <Link href="/playground" className={ctl.primary}><Sparkles className="size-4" /> Buka Playground</Link>
          </>
        }
      />

      {/* ── Hari ini ── */}
      <section className="space-y-3">
        <GroupTitle icon={CalendarDays}>Hari ini</GroupTitle>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-3 xl:grid-cols-6">
          <Stat label="Request hari ini" icon={Activity} tone="primary" href="/logs" value={s ? fmt(s.today) : sk("w-12")} hint={s ? `${fmt(s.ok_today)} sukses` : undefined} />
          <Stat label="Tingkat sukses" icon={CircleCheck} tone={rate === null || rate >= 0.95 ? "success" : "warning"}
            value={s ? (rate === null ? "—" : `${Math.round(rate * 100)}%`) : sk("w-16")} hint={s ? (s.today ? `${fmt(s.errors_today)} error` : "belum ada request") : undefined} />
          <Stat label="Rata-rata durasi" icon={Timer} tone="info" value={s ? (s.today ? `${(s.avg_ms_today / 1000).toFixed(1)}s` : "—") : sk("w-16")} hint="per request hari ini" />
          <Stat label="Sisa kuota grok-3" icon={Gauge} href="/models"
            tone={s?.quota_total && s.quota_remaining !== null && s.quota_remaining / s.quota_total < 0.25 ? "warning" : "success"}
            value={s ? (s.quota_total ? <>{fmt(s.quota_remaining)}<span className="text-base font-semibold text-text-subtle">/{fmt(s.quota_total)}</span></> : "—") : sk("w-16")}
            hint={s ? (s.quota_total ? "request lagi · semua akun" : "membaca kuota…") : undefined} />
          <Stat label="Token hari ini" icon={Hash} tone="info" href="/logs" value={s ? fmt(s.tokens_today) : sk("w-16")} hint="estimasi ±4 karakter/token" />
          <Stat label="Pool aktif" icon={Layers} tone="success" href="/accounts" value={s ? fmt(s.pool_size) : sk("w-10")} hint={s ? `${fmt(s.valid)} valid dari ${fmt(s.total)} akun` : undefined} />
        </div>
        {st.error && !s && (
          <div className="border border-border-subtle bg-surface"><ErrorBox msg={st.error} onRetry={refresh} busy={busy} className="py-6" /></div>
        )}
      </section>

      {/* ── Perlu tindakan ── */}
      <section className="space-y-3">
        <GroupTitle icon={Bell} extra={s ? (total ? <Chip tone="warning">{fmt(total)} perlu dicek</Chip> : <Chip tone="success">Beres semua</Chip>) : undefined}>
          Perlu tindakan
        </GroupTitle>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 sm:gap-3">
          {tiles.map((t) => (
            <Stat key={t.label} label={t.label} href={t.href} icon={t.icon} tone={t.tone} dim={!t.n}
              value={s ? fmt(t.n) : sk("w-10")} hint={s ? (t.n ? "ketuk untuk memproses" : "tidak ada") : undefined} />
          ))}
        </div>
      </section>

      {/* ── Request terakhir + pemakaian akun (kiri) · pintasan (kanan) ── */}
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_22rem] 2xl:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="min-w-0 space-y-5">
          <Section icon={ScrollText} title="Request terakhir" desc="8 request terbaru lewat /v1/chat/completions" flush
            actions={<Link href="/logs" className={ctl.ghost}>Semua log <ChevronRight className="size-4" /></Link>}>
            {!lg.data ? (
              lg.error ? <ErrorBox msg={lg.error} onRetry={() => logsRes.refresh()} /> :
                <div className="space-y-px p-1">{Array.from({ length: 4 }, (_, i) => <Skel key={i} className="h-12" />)}</div>
            ) : !lg.data.length ? (
              <Empty emoji="📭" title="Belum ada request">Coba kirim pesan dari Playground atau client OpenAI-mu.</Empty>
            ) : (
              <ul className="divide-y divide-border-subtle">
                {lg.data.slice(0, 8).map((l) => (
                  <li key={l.id} className="flex items-center gap-3 px-4 py-2.5 sm:px-5">
                    <IconTile icon={l.success ? CircleCheck : AlertTriangle} tone={l.success ? "success" : "danger"} size="xs" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-[13px] text-text-main">{l.account_email}</span>
                      <span className="block truncate text-xs text-text-muted">{l.success ? l.model : l.error_msg || "Error"}</span>
                    </span>
                    <Chip tone="muted">{l.model}</Chip>
                    <span className="w-14 shrink-0 text-right text-xs tabular-nums text-text-muted">{(l.duration_ms / 1000).toFixed(1)}s</span>
                    <span className="hidden w-20 shrink-0 text-right text-xs text-text-subtle sm:block">{relTime(l.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section icon={Layers} tone="success" title="Kuota akun" desc="Sisa kuota grok-3 per akun — akun habis kuota otomatis dilewati pool"
            actions={<Link href="/models" className={ctl.ghost}>Detail <ChevronRight className="size-4" /></Link>}>
            {!pool.data ? <div className="space-y-3">{Array.from({ length: 2 }, (_, i) => <Skel key={i} className="h-9" />)}</div>
              : !accs.length ? <Empty emoji="🫙" title="Belum ada akun valid" action={<Link href="/accounts?f=invalid" className={ctl.primary}><UserPlus className="size-4" /> Login akun</Link>}>Login akun dulu di menu Akun agar masuk pool.</Empty>
              : (
                <ul className="space-y-3.5">
                  {accs.map((a) => {
                    const l = qa.find((x) => x.email === a.email)?.models?.["grok-3"];
                    return (
                      <li key={a.id} className="space-y-1.5">
                        <div className="flex items-center gap-2 text-[13px]">
                          <span className="min-w-0 flex-1 truncate font-mono text-text-main">{a.email}</span>
                          {!a.valid ? <Chip tone="danger">Invalid</Chip> : l?.remaining === 0 ? <Chip tone="warning">Habis</Chip> : <Chip tone="success">Valid</Chip>}
                          <span className="w-20 text-right tabular-nums">
                            {l ? <><b className="font-semibold">{fmt(l.remaining)}</b><span className="text-text-subtle">/{fmt(l.total)}</span></> : <span className="text-text-subtle">—</span>}
                          </span>
                        </div>
                        <Meter value={l ? l.remaining / l.total : 0} tone={!l || !a.valid ? "muted" : l.remaining === 0 ? "danger" : l.remaining / l.total < 0.25 ? "warning" : "success"} />
                      </li>
                    );
                  })}
                </ul>
              )}
          </Section>
        </div>

        <div className="min-w-0 space-y-5 xl:sticky xl:top-20">
          <Section icon={Zap} title="Pintasan" desc="Yang paling sering dipakai" flush>
            <nav aria-label="Pintasan admin" className="divide-y divide-border-subtle">
              <Shortcut href="/accounts?add=1" icon={UserPlus} title="Tambah akun" desc="Satu akun Grok (email + password)" />
              <Shortcut href="/accounts?bulk=1" icon={Upload} tone="info" title="Bulk import akun" desc="Tempel banyak email:password sekaligus" />
              <Shortcut href="/accounts?f=invalid" icon={LogIn} tone="warning" title="Login akun invalid" desc="Ambil cookie baru via Camoufox" value={s ? fmt(invalid) : undefined} />
              <Shortcut href="/keys?new=1" icon={KeyRound} tone="success" title="Buat API key" desc="Key baru untuk aplikasi / client" />
              <Shortcut href="/playground" icon={Sparkles} title="Playground" desc="Tes chat langsung ke Grok" />
              <Shortcut href="/logs" icon={ScrollText} tone="muted" title="Usage logs" desc="100 request terakhir" />
            </nav>
          </Section>
          <p className="flex items-start gap-2 border border-border-subtle bg-surface-2/40 px-3 py-2.5 text-xs text-text-muted">
            <Info className="mt-px size-4 shrink-0 text-info" />
            <span>Data diperbarui otomatis tiap 10 detik selama tab ini terbuka. &ldquo;Hari ini&rdquo; dihitung per tanggal WIB.</span>
          </p>
        </div>
      </div>
    </div>
  );
}

"use client";

/**
 * PRIMITIF UI PANEL ADMIN — port dari Waroengkuy Admin (components/admin/ui.tsx + components/ui/icon-tile.tsx).
 * Satu sumber gaya: kartu datar bergaris, sudut kotak, eyebrow biru, kotak ikon bernada lembut. Warna HANYA token tema.
 * Yg memang bulat (chip, titik status, lencana angka, avatar) pakai `rounded-full`.
 */
import Link from "next/link";
import { useEffect } from "react";
import { ArrowDown, ArrowLeft, ArrowUp, ArrowUpDown, ArrowUpRight, RefreshCw, Search, Unplug, X, type LucideIcon } from "lucide-react";
import { cn } from "@/app/lib/utils";

const TONE = {
  muted: "bg-surface-2 text-text-muted",
  primary: "bg-primary/10 text-primary",
  success: "bg-success/10 text-success",
  warning: "bg-warning/12 text-warning",
  danger: "bg-danger/10 text-danger",
  info: "bg-info/10 text-info",
} as const;
export type Tone = keyof typeof TONE;
const SOLID: Record<Tone, string> = { muted: "bg-text-subtle", primary: "bg-primary", success: "bg-success", warning: "bg-warning", danger: "bg-danger", info: "bg-info" };

const focusRing = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

const TILE = { xs: ["size-7", "size-3.5"], sm: ["size-8", "size-4"], md: ["size-10", "size-5"], lg: ["size-12", "size-6"] } as const;

/** Kotak ikon persegi bernada (kepala kartu, statistik, pintasan). */
export function IconTile({ icon: Icon, tone = "primary", size = "md", className }: { icon: LucideIcon; tone?: Tone; size?: keyof typeof TILE; className?: string }) {
  const [box, ic] = TILE[size];
  return <span aria-hidden className={cn("grid shrink-0 place-items-center", box, TONE[tone], className)}><Icon className={ic} strokeWidth={2} /></span>;
}

/** Judul halaman: kembali opsional, kotak ikon, eyebrow biru, judul besar, deskripsi, aksi. */
export function PageHead({ title, desc, eyebrow, actions, back, icon, tone = "primary" }: {
  title: React.ReactNode; desc?: React.ReactNode; eyebrow?: string; actions?: React.ReactNode; back?: { href: string; label: string }; icon?: LucideIcon; tone?: Tone;
}) {
  return (
    <div className="mb-5 space-y-3 sm:mb-6">
      {back && (
        <Link href={back.href} className={cn("inline-flex items-center gap-1.5 text-sm font-medium text-text-muted transition-colors hover:text-primary", focusRing)}>
          <ArrowLeft className="size-4" /> {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div className="flex min-w-0 items-center gap-3 sm:gap-4">
          {icon && <div aria-hidden className="hidden shrink-0 min-[400px]:block"><IconTile icon={icon} tone={tone} size="lg" className="sm:size-14" /></div>}
          <div className="min-w-0">
            {eyebrow && <p className="truncate text-xs font-semibold uppercase tracking-[0.15em] text-primary">{eyebrow}</p>}
            <h1 className={cn("text-2xl font-bold tracking-tight text-text-main sm:text-[28px] sm:leading-9 [overflow-wrap:anywhere]", eyebrow && "mt-1")}>{title}</h1>
            {desc && <p className="mt-1 max-w-3xl text-sm text-text-muted">{desc}</p>}
          </div>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}

/** Judul kelompok kecil (eyebrow biru + ikon), mis. "Hari ini", "Perlu tindakan". */
export function GroupTitle({ icon: Icon, children, extra }: { icon: LucideIcon; children: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.15em] text-primary"><Icon className="size-4" /> {children}</h2>
      {extra}
    </div>
  );
}

/** Kartu bagian: kepala (kotak ikon + judul + deskripsi + aksi) lalu isi. `flush` = isi tanpa padding (tabel/daftar). */
export function Section({ title, desc, icon, tone = "primary", actions, children, flush, className }: {
  title?: React.ReactNode; desc?: React.ReactNode; icon?: LucideIcon; tone?: Tone; actions?: React.ReactNode; children: React.ReactNode; flush?: boolean; className?: string;
}) {
  return (
    <section className={cn("min-w-0 border border-border-subtle bg-surface", className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border-subtle px-4 py-3 sm:px-5">
          {icon && <IconTile icon={icon} tone={tone} size="sm" />}
          <div className="min-w-0 flex-1">
            {title && <h2 className="text-[15px] font-semibold leading-tight tracking-tight text-text-main">{title}</h2>}
            {desc && <p className="mt-0.5 text-xs text-text-muted">{desc}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-1.5">{actions}</div>}
        </div>
      )}
      <div className={flush ? "" : "p-4 sm:p-5"}>{children}</div>
    </section>
  );
}

/** Angka ringkas (KPI). href → bisa diketuk. `dim` = redupkan (mis. antrean 0). */
export function Stat({ label, value, hint, icon, tone = "muted", href, dim, className }: {
  label: string; value: React.ReactNode; hint?: React.ReactNode; icon?: LucideIcon; tone?: Tone; href?: string; dim?: boolean; className?: string;
}) {
  const ext = !!href && /^https?:\/\//.test(href);
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <p className="line-clamp-2 min-w-0 text-xs font-medium leading-snug text-text-muted">{label}</p>
        {icon ? <IconTile icon={icon} tone={dim ? "muted" : tone} size="sm" className={cn("-mr-0.5 -mt-0.5", dim && "opacity-70")} />
          : ext && <ArrowUpRight className="size-3.5 shrink-0 text-text-subtle" />}
      </div>
      <div className={cn("mt-1.5 text-2xl font-bold tabular-nums tracking-tight", dim ? "text-text-subtle" : "text-text-main")}>{value}</div>
      {hint && <p className="mt-0.5 truncate text-[11px] text-text-subtle">{hint}</p>}
    </>
  );
  const cls = cn("block min-w-0 border border-border-subtle bg-surface p-3.5 transition-colors sm:p-4", className);
  if (!href) return <div className={cls}>{body}</div>;
  return ext
    ? <a href={href} target="_blank" rel="noopener noreferrer" className={cn(cls, "hover:border-primary/40", focusRing)}>{body}</a>
    : <Link href={href} className={cn(cls, "hover:border-primary/40", focusRing)}>{body}</Link>;
}

/** Label kecil berwarna (pill bulat). */
export function Chip({ tone = "muted", children, className, title }: { tone?: Tone; children: React.ReactNode; className?: string; title?: string }) {
  return <span title={title} className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold leading-4", TONE[tone], className)}>{children}</span>;
}

/** Gelembung angka bulat (lencana menu). n ≤ 0 → tak tampil. */
export function CountPill({ n, tone = "warning", solid, className }: { n: number; tone?: "warning" | "danger" | "primary" | "muted"; solid?: boolean; className?: string }) {
  if (!n || n <= 0) return null;
  const soft = { warning: "bg-warning/15 text-warning", danger: "bg-danger/15 text-danger", primary: "bg-primary/10 text-primary", muted: "bg-surface-2 text-text-muted" }[tone];
  const hard = { warning: "bg-warning text-bg", danger: "bg-danger text-bg", primary: "bg-primary text-primary-foreground", muted: "bg-text-subtle text-bg" }[tone];
  return <span className={cn("inline-grid min-w-5 place-items-center rounded-full px-1.5 text-[10px] font-bold leading-5 tabular-nums", solid ? hard : soft, className)}>{n > 99 ? "99+" : n}</span>;
}

/** Titik status bulat. `pulse` = berdenyut. */
export function Dot({ tone = "primary", pulse, className }: { tone?: Tone; pulse?: boolean; className?: string }) {
  return (
    <span aria-hidden className={cn("relative inline-flex size-2 shrink-0", className)}>
      {pulse && <span className={cn("absolute inset-0 rounded-full opacity-60 motion-safe:animate-ping", SOLID[tone])} />}
      <span className={cn("relative inline-block size-full rounded-full", SOLID[tone])} />
    </span>
  );
}

/** Bar progres tipis (rasio 0..1). */
export function Meter({ value, tone = "primary", className }: { value: number; tone?: Tone; className?: string }) {
  const w = Math.max(0, Math.min(1, Number(value) || 0));
  return (
    <span aria-hidden className={cn("block h-1.5 w-full overflow-hidden rounded-full bg-surface-2", className)}>
      <span className={cn("block h-full rounded-full", SOLID[tone])} style={{ width: `${w > 0 ? Math.max(2, Math.round(w * 100)) : 0}%` }} />
    </span>
  );
}

/** Keadaan kosong/beres: emoji polos atau ikon + judul + teks + aksi. */
export function Empty({ children, icon: Icon, emoji, title, action, className }: {
  children?: React.ReactNode; icon?: LucideIcon; emoji?: string; title?: React.ReactNode; action?: React.ReactNode; className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-2 px-4 py-10 text-center", className)}>
      {emoji ? <span aria-hidden className="mb-1 text-[40px] leading-none">{emoji}</span>
        : Icon && <span aria-hidden className="grid size-10 place-items-center bg-surface-2 text-text-subtle"><Icon className="size-5" /></span>}
      {title && <p className="text-sm font-semibold text-text-main">{title}</p>}
      {children && <div className="max-w-sm text-sm text-text-muted">{children}</div>}
      {action}
    </div>
  );
}

/** Galat memuat: pesan + tombol coba lagi. */
export function ErrorBox({ msg, onRetry, className, busy }: { msg?: string | null; onRetry?: () => void; className?: string; busy?: boolean }) {
  return (
    <div role="alert" className={cn("flex flex-col items-center gap-2 px-4 py-10 text-center", className)}>
      <IconTile icon={Unplug} tone="danger" size="lg" />
      <p className="text-sm font-semibold text-text-main">Gagal memuat data</p>
      <p className="max-w-sm text-xs text-text-muted">{msg || "Periksa koneksi ke backend lalu coba lagi."}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} disabled={busy} className={cn(ctl.btn, "mt-1")}>
          <RefreshCw className={cn("size-3.5", busy && "motion-safe:animate-spin")} /> Coba lagi
        </button>
      )}
    </div>
  );
}

/** Kotak abu bersapu kilau (memuat). */
export function Skel({ className }: { className?: string }) {
  return <div aria-hidden className={cn("sb-shimmer", className)} />;
}

/** Tombol Segarkan — ikon berputar saat memperbarui. */
export function RefreshBtn({ onClick, busy, label = "Segarkan" }: { onClick: () => void; busy?: boolean; label?: string }) {
  return (
    <button type="button" onClick={onClick} disabled={busy} className={ctl.btn} aria-label={label} title={busy ? "Memperbarui…" : label}>
      <RefreshCw className={cn("size-4", busy && "motion-safe:animate-spin")} />
      <span className="hidden sm:inline">{busy ? "Memperbarui…" : label}</span>
    </button>
  );
}

/** Kelas tabel bersama — kepala berlatar & lengket, baris bergaris, angka rata kanan via `num`. */
export const tbl = {
  wrap: "relative w-full overflow-x-auto",
  scroll: "relative w-full overflow-auto overscroll-x-contain max-h-[calc(100dvh-13rem)]",
  table: "w-full min-w-[720px] border-collapse text-sm",
  th: "sticky top-0 z-[1] whitespace-nowrap border-b border-border-subtle bg-surface-2 px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-text-subtle",
  td: "border-b border-border-subtle px-3 py-2.5 align-middle text-text-main",
  num: "text-right tabular-nums",
  row: "transition-colors hover:bg-surface-2/50",
};

/** Input & tombol seragam. */
export const ctl = {
  input: "h-9 min-w-0 border border-border bg-surface px-3 text-sm text-text-main outline-none placeholder:text-text-subtle focus:border-primary/50 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary",
  btn: "inline-flex h-9 items-center justify-center gap-1.5 whitespace-nowrap border border-border bg-surface px-3 text-[13px] font-semibold text-text-main transition-colors hover:border-primary/40 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
  primary: "inline-flex h-9 items-center justify-center gap-1.5 whitespace-nowrap bg-primary px-3 text-[13px] font-semibold text-primary-foreground transition-colors hover:bg-brand-600 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
  danger: "inline-flex h-9 items-center justify-center gap-1.5 whitespace-nowrap border border-border bg-surface px-3 text-[13px] font-semibold text-danger transition-colors hover:border-danger/40 hover:bg-danger/5 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
  ghost: "inline-flex h-9 items-center justify-center gap-1.5 whitespace-nowrap px-2.5 text-[13px] font-medium text-text-muted transition-colors hover:bg-surface-2 hover:text-text-main disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
  /** Tombol ikon kecil di baris tabel. */
  icon: "inline-grid size-8 place-items-center border border-border bg-surface text-text-muted transition-colors hover:border-primary/40 hover:text-primary disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
};

/** Chip filter yg bisa ditekan (pill bulat). */
export const pillCls = (on: boolean) =>
  cn("inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
    on ? "border-primary bg-primary/10 text-primary" : "border-border bg-surface text-text-muted hover:border-primary/40 hover:text-text-main");

export function FilterChip({ on, onClick, count, tone, children }: { on: boolean; onClick: () => void; count?: number; tone?: "danger" | "warning"; children: React.ReactNode }) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick} className={pillCls(on)}>
      {children}
      {count !== undefined && (
        <span className={cn("tabular-nums", on ? "text-primary" : count && tone === "danger" ? "text-danger" : count && tone === "warning" ? "text-warning" : "text-text-subtle")}>{count.toLocaleString("id-ID")}</span>
      )}
    </button>
  );
}

export type SortState<K extends string> = { key: K; dir: "asc" | "desc" };

/** Kepala kolom yg bisa diklik utk mengurutkan. */
export function SortTh<K extends string>({ k, sort, onSort, num, children }: { k: K; sort: SortState<K>; onSort: (k: K) => void; num?: boolean; children: React.ReactNode }) {
  const on = sort.key === k;
  const Icon = !on ? ArrowUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th className={cn(tbl.th, num && "text-right")} aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}>
      <button type="button" onClick={() => onSort(k)} className={cn("inline-flex items-center gap-1 uppercase hover:text-primary", on && "text-primary", num && "flex-row-reverse")}>
        {children} <Icon className={cn("size-3", !on && "opacity-40")} />
      </button>
    </th>
  );
}

/** Kotak cari ber-ikon + tombol hapus. */
export function SearchBox({ value, onChange, placeholder, className }: { value: string; onChange: (v: string) => void; placeholder?: string; className?: string }) {
  return (
    <label className={cn("relative min-w-0 flex-1", className)}>
      <span className="sr-only">{placeholder || "Cari"}</span>
      <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-subtle" />
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder || "Cari…"} className={cn(ctl.input, "w-full pl-9 pr-9")} />
      {value && (
        <button type="button" onClick={() => onChange("")} aria-label="Hapus pencarian" className="absolute right-1 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center text-text-subtle hover:text-text-main">
          <X className="size-4" />
        </button>
      )}
    </label>
  );
}

/** Dialog kotak gaya kartu Section. Esc / klik latar = tutup. */
export function Modal({ title, desc, icon, onClose, children, footer }: {
  title: string; desc?: string; icon?: LucideIcon; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode;
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.target === e.currentTarget && onClose()}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 backdrop-blur-[2px] sm:items-center sm:p-4">
      <div className="toast-in w-full max-w-md border border-border-subtle bg-surface shadow-[var(--shadow-elevated)]">
        <div className="flex items-center gap-3 border-b border-border-subtle px-4 py-3 sm:px-5">
          {icon && <IconTile icon={icon} size="sm" />}
          <div className="min-w-0 flex-1">
            <h2 className="text-[15px] font-semibold leading-tight text-text-main">{title}</h2>
            {desc && <p className="mt-0.5 text-xs text-text-muted">{desc}</p>}
          </div>
          <button type="button" onClick={onClose} aria-label="Tutup" className={ctl.ghost}><X className="size-4" /></button>
        </div>
        <div className="space-y-3 p-4 sm:p-5">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-border-subtle bg-surface-2/40 px-4 py-3 sm:px-5">{footer}</div>}
      </div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-text-main">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-text-subtle">{hint}</span>}
    </label>
  );
}

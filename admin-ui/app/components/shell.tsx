"use client";

/**
 * Kerangka PANEL ADMIN Grok2API — port dari Waroengkuy Admin (components/admin/wk-admin-shell.tsx).
 * Header sticky (logo + label ADMIN, cari, Live, tema, keluar) · desktop (lg+) sidebar kiri lengket yg bisa diciutkan
 * (disimpan per-browser) · tablet baris tab bergaris bawah · HP bottom bar (`data-bnav`).
 * Lencana menu = antrean yg perlu perhatian (akun invalid / login gagal, error hari ini) dari cache bersama lib/store.
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useRef, useSyncExternalStore } from "react";
import { useTheme } from "next-themes";
import {
  Bot, Copy, Cpu, ExternalLink, House, Terminal, KeyRound, LogOut, Moon, PanelLeftClose, PanelLeftOpen, ScrollText, Search, Sparkles, Sun, Users, type LucideIcon,
} from "lucide-react";
import { toast, Toaster } from "./toast";
import { CountPill, Dot, type Tone } from "./ui";
import { quotaRes, statsRes, useRes } from "@/app/lib/store";
import { cn } from "@/app/lib/utils";

// ── Sidebar diciutkan (desktop) — localStorage via useSyncExternalStore (aman SSR). ──
const COLLAPSE_KEY = "g2a-admin:nav-collapsed";
const collapseSubs = new Set<() => void>();
const readCollapsed = () => { try { return localStorage.getItem(COLLAPSE_KEY) === "1"; } catch { return false; } };
const subCollapsed = (cb: () => void) => {
  collapseSubs.add(cb);
  window.addEventListener("storage", cb);
  return () => { collapseSubs.delete(cb); window.removeEventListener("storage", cb); };
};
const setCollapsed = (v: boolean) => {
  try { localStorage.setItem(COLLAPSE_KEY, v ? "1" : "0"); } catch { /* mode privat */ }
  collapseSubs.forEach((f) => f());
};
const useMounted = () => useSyncExternalStore(() => () => {}, () => true, () => false);

type NavKey = "home" | "accounts" | "models" | "keys" | "logs" | "console" | "playground";
const NAV: { key: NavKey; href: string; label: string; short: string; icon: LucideIcon }[] = [
  { key: "home", href: "/", label: "Beranda", short: "Beranda", icon: House },
  { key: "accounts", href: "/accounts", label: "Akun Grok", short: "Akun", icon: Users },
  { key: "models", href: "/models", label: "Model & Kuota", short: "Kuota", icon: Cpu },
  { key: "keys", href: "/keys", label: "API Keys", short: "Keys", icon: KeyRound },
  { key: "logs", href: "/logs", label: "Usage Logs", short: "Logs", icon: ScrollText },
  { key: "console", href: "/console", label: "Konsol", short: "Konsol", icon: Terminal },
  { key: "playground", href: "/playground", label: "Playground", short: "Tes", icon: Sparkles },
];
const isOn = (href: string, path: string) => (href === "/" ? path === "/" : path === href || path.startsWith(href + "/"));

type Live = "live" | "connecting" | "off";
const LIVE_META: Record<Live, { tone: Tone; label: string; hint: string }> = {
  live: { tone: "success", label: "Live", hint: "Backend tersambung — data diperbarui otomatis tiap 10 detik." },
  connecting: { tone: "warning", label: "Menyambung…", hint: "Menyambung ke backend…" },
  off: { tone: "muted", label: "Offline", hint: "Backend tidak merespons — cek server.py (port 8168)." },
};

export function LiveIndicator({ status }: { status: Live }) {
  const m = LIVE_META[status];
  return (
    <span role="status" title={m.hint} className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-border-subtle bg-surface px-2.5 text-xs font-semibold text-text-muted">
      <Dot tone={m.tone} pulse={status === "live"} />
      <span className="hidden md:inline">{m.label}</span>
      <span className="sr-only md:hidden">{m.label}</span>
    </span>
  );
}

/** Ganti tema dgn lingkaran menyebar dari tombol (View Transitions, sama dgn Waroengkuy). */
function switchTheme(next: "light" | "dark", el: HTMLElement, setTheme: (t: string) => void) {
  const apply = () => {
    const d = document.documentElement;
    d.classList.toggle("dark", next === "dark");
    d.classList.toggle("light", next === "light");
    d.style.colorScheme = next;
    setTheme(next);
  };
  const doc = document as Document & { startViewTransition?: (cb: () => void) => { ready: Promise<void> } };
  if (!doc.startViewTransition || matchMedia("(prefers-reduced-motion: reduce)").matches) return apply();
  const r = el.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  doc.startViewTransition(apply).ready.then(() => {
    document.documentElement.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 520, easing: "cubic-bezier(.4,0,.2,1)", pseudoElement: "::view-transition-new(root)" },
    );
  }).catch(() => {});
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useMounted();
  const dark = mounted && resolvedTheme === "dark";
  const next = dark ? "Terang" : "Gelap";
  const Icon = dark ? Moon : Sun;
  return (
    <button type="button" aria-label={`Ganti ke tema ${next}`} title={`Ganti ke tema ${next}`}
      onClick={(e) => switchTheme(dark ? "light" : "dark", e.currentTarget, setTheme)}
      className="grid size-9 shrink-0 place-items-center border border-border bg-surface text-text-main transition-colors hover:border-primary/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
      <Icon className="size-4" />
    </button>
  );
}

/** Cari akun dari header → /accounts?q=… ("/" = fokus). */
function HeaderSearch() {
  const router = useRouter();
  const ref = useRef<HTMLInputElement>(null);
  return (
    <form role="search" onSubmit={(e) => { e.preventDefault(); const q = ref.current?.value.trim() || ""; router.push(q ? `/accounts?q=${encodeURIComponent(q)}` : "/accounts"); }}
      className="relative hidden md:block">
      <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-text-subtle" />
      <input ref={ref} type="search" placeholder="Cari akun…" aria-label="Cari akun"
        onKeyDown={(e) => e.key === "Escape" && e.currentTarget.blur()}
        className="h-9 w-48 border border-border bg-surface pl-8 pr-3 text-[13px] text-text-main outline-none placeholder:text-text-subtle focus:border-primary/50 lg:w-60" />
    </form>
  );
}

const box = "inline-flex h-9 items-center gap-1.5 border border-border bg-surface px-2.5 text-[13px] font-semibold text-text-main transition-colors hover:border-primary/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";
const ring = "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary";

export function AdminShell({ children }: { children: React.ReactNode }) {
  const path = usePathname() || "/";
  const collapsed = useSyncExternalStore(subCollapsed, readCollapsed, () => false);
  const stats = useRes(statsRes);
  const quota = useRes(quotaRes);
  const drained = (quota.data?.accounts ?? []).filter((a) => a.models?.["grok-3"]?.remaining === 0).length;
  const live: Live = stats.error ? "off" : stats.data ? "live" : "connecting";

  // Lencana: akun belum siap (invalid) & error hari ini — dari counts server, bukan daftar penuh.
  const bad = stats.data?.invalid ?? 0;
  const badge: Partial<Record<NavKey, { n: number; tone: "danger" | "warning"; what: string }>> = {
    accounts: { n: bad, tone: "danger", what: `${bad} akun belum valid` },
    models: { n: drained, tone: "warning", what: `${drained} akun habis kuota grok-3` },
    logs: { n: stats.data?.errors_today ?? 0, tone: "warning", what: `${stats.data?.errors_today ?? 0} error hari ini` },
  };

  const logout = async () => {
    await fetch("/admin/api/logout", { method: "POST", credentials: "include" }).catch(() => {});
    window.location.href = "/login";
  };
  const copyBase = async () => {
    try { await navigator.clipboard.writeText(`${location.origin}/v1`); toast.success("Base URL disalin"); }
    catch { toast.error("Gagal menyalin"); }
  };

  return (
    <div className="min-h-dvh bg-bg text-text-main">
      <a href="#g2a-main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:bg-primary focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:text-primary-foreground">
        Lewati ke konten
      </a>

      {/* ── Header ── */}
      <header className="sticky top-0 z-30 border-b border-border/60 bg-bg/80 backdrop-blur-md">
        <div className="flex h-16 w-full items-center gap-2 px-4 sm:gap-3 sm:px-6 lg:px-5">
          <Link href="/" aria-label="Beranda admin" className="flex shrink-0 items-center gap-2 outline-offset-2 focus-visible:outline-2 focus-visible:outline-primary sm:gap-2.5 lg:w-[196px]">
            <Bot className="size-[30px] shrink-0 text-primary" strokeWidth={1.75} />
            <span className="hidden text-base font-bold tracking-tight text-text-main min-[400px]:inline sm:text-[17px]">Grok2API</span>
            <span className="shrink-0 rounded-full bg-primary px-1.5 py-[3px] text-[8.5px] font-bold uppercase leading-none tracking-wide text-primary-foreground">Admin</span>
          </Link>
          <div className="min-w-0 flex-1" />
          <div className="flex shrink-0 items-center gap-2">
            <HeaderSearch />
            <LiveIndicator status={live} />
            <ThemeToggle />
            <a href="/v1/models" target="_blank" rel="noopener noreferrer" className={cn(box, "hidden md:inline-flex")} title="Daftar model API (JSON)">
              Models <ExternalLink className="size-3.5" />
            </a>
            <button type="button" onClick={logout} title="Keluar" aria-label="Keluar" className={cn(box, "px-2 hover:border-danger/40 hover:text-danger")}>
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </header>

      <div className="flex w-full">
        {/* ── Desktop: sidebar kiri lengket, bisa diciutkan jadi rel ikon. ── */}
        <aside className={cn("sticky top-16 hidden h-[calc(100dvh-4rem)] shrink-0 flex-col border-r border-border/60 bg-bg transition-[width] duration-200 ease-[var(--ease-smooth)] lg:flex",
          collapsed ? "w-16" : "w-[228px]")}>
          <nav aria-label="Menu admin" className="sidebar-scroll min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-2 py-4">
            {!collapsed && <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-[0.15em] text-text-subtle">Menu</p>}
            <ul className="space-y-0.5">
              {NAV.map(({ key, href, label, icon: Icon }) => {
                const on = isOn(href, path);
                const b = badge[key];
                return (
                  <li key={key}>
                    <Link href={href} aria-current={on ? "page" : undefined} title={collapsed ? (b?.n ? `${label} — ${b.what}` : label) : b?.n ? b.what : undefined}
                      aria-label={collapsed ? label : undefined}
                      className={cn("group relative flex h-10 items-center gap-3 text-[13px] transition-colors", ring, collapsed ? "justify-center" : "px-3",
                        on ? "bg-primary/10 font-semibold text-primary" : "font-medium text-text-muted hover:bg-surface-2 hover:text-text-main")}>
                      {on && <span aria-hidden className="absolute inset-y-0 left-0 w-0.5 bg-primary" />}
                      <span className="relative shrink-0">
                        <Icon className="size-[18px] transition-transform duration-150 group-hover:scale-110" />
                        {collapsed && b && <CountPill n={b.n} tone={b.tone} solid className="absolute -right-3 -top-2.5 ring-2 ring-bg" />}
                      </span>
                      {!collapsed && <span className="min-w-0 flex-1 truncate">{label}</span>}
                      {!collapsed && b && <CountPill n={b.n} tone={b.tone} solid />}
                    </Link>
                  </li>
                );
              })}
            </ul>
            <div className={cn("mt-5 border-t border-border-subtle pt-4", collapsed && "mt-4 pt-3")}>
              {!collapsed && <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-[0.15em] text-text-subtle">Pintasan</p>}
              <ul className="space-y-0.5">
                <li>
                  <button type="button" onClick={copyBase} title="Salin Base URL API (…/v1) untuk client OpenAI"
                    className={cn("flex h-9 w-full items-center gap-3 text-[13px] font-medium text-text-muted transition-colors hover:bg-surface-2 hover:text-text-main", ring, collapsed ? "justify-center" : "px-3")}>
                    <Copy className="size-4 shrink-0" />
                    {!collapsed && <span className="min-w-0 flex-1 truncate text-left">Salin Base URL</span>}
                    {!collapsed && <span className="text-[10px] font-semibold uppercase tracking-wider text-text-subtle">/v1</span>}
                  </button>
                </li>
                <li>
                  <a href="/v1/models" target="_blank" rel="noopener noreferrer" title={collapsed ? "Daftar model" : undefined}
                    className={cn("flex h-9 items-center gap-3 text-[13px] font-medium text-text-muted transition-colors hover:bg-surface-2 hover:text-text-main", ring, collapsed ? "justify-center" : "px-3")}>
                    <Bot className="size-4 shrink-0" />
                    {!collapsed && <span className="min-w-0 flex-1 truncate">Daftar model</span>}
                    {!collapsed && <ExternalLink className="size-3.5 shrink-0 text-text-subtle" />}
                  </a>
                </li>
              </ul>
            </div>
          </nav>
          <div className="border-t border-border/60 p-2">
            {!collapsed && (
              <div className="mb-1 flex items-center gap-2.5 px-2 py-1.5">
                <span aria-hidden className="grid size-8 shrink-0 place-items-center rounded-full bg-primary/10 text-sm font-bold text-primary">A</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-semibold text-text-main">Admin</span>
                  <span className="flex items-center gap-1.5 text-[11px] text-text-subtle"><Dot tone={LIVE_META[live].tone} className="size-1.5" /> Grok2API · {LIVE_META[live].label}</span>
                </span>
              </div>
            )}
            <button type="button" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? "Lebarkan menu" : "Ciutkan menu"} title={collapsed ? "Lebarkan menu" : "Ciutkan menu"}
              className={cn("flex h-9 w-full items-center gap-2 text-xs font-medium text-text-muted transition-colors hover:bg-surface-2 hover:text-text-main", ring, collapsed ? "justify-center" : "px-3")}>
              {collapsed ? <PanelLeftOpen className="size-4" /> : <><PanelLeftClose className="size-4" /> Ciutkan menu</>}
            </button>
          </div>
        </aside>

        <div className="min-w-0 flex-1">
          {/* ── Tablet: baris tab bergaris bawah biru. ── */}
          <nav aria-label="Menu admin" className="sticky top-16 z-20 hidden border-b border-border/60 bg-bg sm:block lg:hidden">
            <ul className="flex w-full gap-0.5 overflow-x-auto px-2 [scrollbar-width:none] sm:px-4 [&::-webkit-scrollbar]:hidden">
              {NAV.map(({ key, href, label, icon: Icon }) => {
                const on = isOn(href, path);
                const b = badge[key];
                return (
                  <li key={key} className="shrink-0">
                    <Link href={href} aria-current={on ? "page" : undefined}
                      className={cn("inline-flex h-11 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 text-[13px] font-semibold transition-colors", ring,
                        on ? "border-primary text-primary" : "border-transparent text-text-muted hover:text-primary")}>
                      <Icon className="size-4" /> {label}
                      {b && <CountPill n={b.n} tone={b.tone} solid />}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <main id="g2a-main" className="w-full px-4 pb-8 pt-5 sm:px-6 sm:pt-6 lg:px-8 lg:pt-7 2xl:px-10">{children}</main>
        </div>
      </div>

      {/* ── HP: bottom bar. ── */}
      <nav data-bnav aria-label="Menu admin" className="fixed inset-x-0 bottom-0 z-30 border-t border-border/70 bg-bg/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md sm:hidden">
        <ul className="grid grid-cols-7">
          {NAV.map(({ key, href, short, label, icon: Icon }) => {
            const on = isOn(href, path);
            const b = badge[key];
            return (
              <li key={key}>
                <Link href={href} aria-current={on ? "page" : undefined} aria-label={b?.n ? `${label} — ${b.what}` : label}
                  className={cn("relative flex h-[4.5rem] w-full flex-col items-center justify-center gap-1 text-[11px] transition-colors", ring,
                    on ? "font-semibold text-primary" : "font-medium text-text-muted hover:text-text-main")}>
                  <span className={cn("relative grid h-7 w-12 place-items-center rounded-full transition-colors", on && "bg-primary/12")}>
                    <Icon className="size-5" strokeWidth={on ? 2.25 : 2} />
                    {b && <CountPill n={b.n} tone={b.tone} solid className="absolute -right-1 -top-1.5" />}
                  </span>
                  <span className="max-w-full truncate px-0.5">{short}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <Toaster />
    </div>
  );
}

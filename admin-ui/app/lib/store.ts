"use client";

/**
 * Cache data panel bersama (ala lib/panel-cache Waroengkuy): satu resource per endpoint, dipakai shell (lencana menu,
 * indikator Live) & halaman sekaligus → tanpa request ganda. Polling hanya jalan selama ada pemakai & tab terlihat.
 * ponytail: in-memory per tab, tanpa SWR/React Query — cukup utk 4 endpoint.
 */
import { useSyncExternalStore } from "react";
import { api } from "./api";

export type Snap<T> = { data?: T; error?: string; validating: boolean; at: number };

function resource<T>(url: string, everyMs: number) {
  let snap: Snap<T> = { validating: false, at: 0 };
  const subs = new Set<() => void>();
  let timer: ReturnType<typeof setInterval> | null = null;
  const set = (p: Partial<Snap<T>>) => { snap = { ...snap, ...p }; subs.forEach((f) => f()); };

  async function refresh() {
    set({ validating: true });
    try {
      set({ data: await api<T>(url), error: undefined, validating: false, at: Date.now() });
    } catch (e) {
      set({ error: (e as Error).message, validating: false });
    }
  }
  const tick = () => { if (document.visibilityState === "visible") void refresh(); };

  return {
    refresh,
    subscribe(cb: () => void) {
      subs.add(cb);
      if (subs.size === 1) {
        if (!snap.at || Date.now() - snap.at > everyMs) void refresh();
        timer = setInterval(tick, everyMs);
        document.addEventListener("visibilitychange", tick);
      }
      return () => {
        subs.delete(cb);
        if (!subs.size && timer) { clearInterval(timer); timer = null; document.removeEventListener("visibilitychange", tick); }
      };
    },
    get: () => snap,
  };
}

const SERVER: Snap<never> = { validating: false, at: 0 };
type Res<T> = ReturnType<typeof resource<T>>;
export function useRes<T>(r: Res<T>): Snap<T> {
  return useSyncExternalStore(r.subscribe, r.get, () => SERVER as Snap<T>);
}

// ── Tipe data backend ──
export type Stats = {
  total: number; valid: number; invalid: number; failed: number; pool_size: number; requests: number;
  today: number; errors_today: number; ok_today: number; avg_ms_today: number; login_busy: number;
  tokens_today: number; quota_remaining: number | null; quota_total: number | null;
};
export type Limit = { remaining: number; total: number; window: number };
export type ImagineLimit = { remaining: number; window: number; available: boolean };
export type QuotaAccount = { id: number; email: string; at?: number; models?: Record<string, Limit>; imagine?: Record<string, ImagineLimit>; error?: string };
export type ChatMode = { mode: string; label: string; ok: boolean; quota: string; aliases: string[] };
export type Quota = { models: string[]; proxy_ok: string[]; ttl: number; modes: ChatMode[]; accounts: QuotaAccount[] };
export type GrokModel = { id: string; mode: string; badge: string; tags: string[]; backend: string; hidden: boolean; proxy: boolean; available: boolean };
export type Catalog = { source: string; defaults: Record<string, string>; models: GrokModel[] };
export type Account = {
  id: number; email: string; user_id: string; valid: number; last_verified: string; last_used: string;
  request_count: number; error_count: number; login_status: string; created_at: string;
};
export type AccountCounts = { all: number; valid: number; invalid: number; failed: number; invalid_pw: number; busy: number };
export type AccountsPage = {
  rows: Account[]; total: number; page: number; page_size: number; counts: AccountCounts;
  req_sum: number; err_sum: number; ever_login: number;
};
/** Fetch one page of accounts (server-side filter/sort/paginate). */
export function fetchAccounts(p: { page?: number; page_size?: number; q?: string; status?: string; sort?: string; dir?: string }): Promise<AccountsPage> {
  const qs = new URLSearchParams();
  if (p.page) qs.set("page", String(p.page));
  if (p.page_size) qs.set("page_size", String(p.page_size));
  if (p.q) qs.set("q", p.q);
  if (p.status && p.status !== "all") qs.set("status", p.status);
  if (p.sort) qs.set("sort", p.sort);
  if (p.dir) qs.set("dir", p.dir);
  return api<AccountsPage>(`/admin/api/accounts?${qs}`);
}
export type ApiKey = { id: number; key: string; name: string; active: number; request_count: number; created_at: string };
export type UsageLog = {
  id: number; account_email: string; api_key: string; model: string; duration_ms: number; success: number; error_msg: string; created_at: string;
  prompt_tokens: number; completion_tokens: number;
};

export const statsRes = resource<Stats>("/admin/api/stats", 10_000);
// Dashboard only needs the active pool (valid accounts), capped — never the full 8k list.
export const poolRes = resource<AccountsPage>("/admin/api/accounts?status=valid&page_size=50&sort=request_count&dir=desc", 15_000);
export const keysRes = resource<ApiKey[]>("/admin/api/keys", 30_000);
export const logsRes = resource<UsageLog[]>("/admin/api/logs", 10_000);
// Backend meng-cache kuota 5 mnt & katalog 10 mnt → polling di sini murah (tak menembak grok.com tiap kali).
export const quotaRes = resource<Quota>("/admin/api/quota", 60_000);
export const modelsRes = resource<Catalog>("/admin/api/models", 600_000);
/** Paksa ambil ulang dari grok.com (lewati cache backend). */
export const forceQuota = async () => { await api("/admin/api/quota?refresh=1"); await quotaRes.refresh(); };

/** "24 jam", "4 jam", "2 jam", "60 mnt". */
export const fmtWindow = (s: number) => (s >= 3600 ? `${Math.round(s / 3600)} jam` : `${Math.round(s / 60)} mnt`);
/** Nama ramah per model Grok. */
export const MODEL_INFO: Record<string, { label: string; note: string }> = {
  "grok-3": { label: "Grok (gratis)", note: "Kuota bersama mode Auto & Fast — dipakai semua request proxy. Saat ini menjawab sebagai Grok 4.5." },
  "grok-4": { label: "Grok 4", note: "Kuota mode Expert/Heavy. Ditolak gateway di akun gratis tanpa token anti-bot browser." },
  "grok-4-1-thinking-1129": { label: "Grok 4.1 Thinking", note: "Mode berpikir. Ditolak gateway (stream_error) — hanya bisa dari browser." },
  "grok-420": { label: "Grok 420", note: "Mode tersembunyi. Ditolak gateway (stream_error)." },
  build: { label: "Grok Build (Beta)", note: "Agen coding/build. Jalan di browser (pakai token anti-bot Castle), ditolak lewat proxy." },
};

export const loginFailed = (s: string) => s === "failed" || s === "invalid" || s.startsWith("error");
export const loginBusy = (s: string) => s === "running" || s === "queued";

export type BulkStatus = {
  running: boolean; total: number; done: number; success: number; invalid: number; failed: number; skipped: number;
  concurrency: number; proxies?: number; elapsed?: number; rate?: number; eta?: number;
  current: string[]; recent: { email: string; status: string; detail: string }[];
};
export type Settings = { concurrency: number; headless: number; login_timeout: number; proxies: string; del_invalid: number; del_failed: number };

export const bulkRes = resource<BulkStatus>("/admin/api/accounts/bulk-login/status", 2000);

export type ConsoleLevel = "ok" | "info" | "warn" | "error";
export type ConsoleEvent = { id: number; ts: string; level: ConsoleLevel; source: string; msg: string };
export const fetchConsole = (after: number) =>
  api<{ events: ConsoleEvent[]; last_id: number; gap: boolean }>(`/admin/api/console?after=${after}`);

export const IMAGINE_LABEL: Record<string, string> = { imagePro: "Gambar Pro", image: "Gambar", imageEdit: "Edit gambar", video: "Video", video720p: "Video 720p" };

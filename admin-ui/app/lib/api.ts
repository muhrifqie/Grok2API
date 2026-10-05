// Fetch helper — same-origin paths proxied to the Python backend (see next.config.ts)
export async function api<T = unknown>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    credentials: "include",
    cache: "no-store",
  });
  if (res.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
    throw new Error("Sesi habis, silakan masuk lagi");
  }
  const txt = await res.text();
  let data: unknown = null;
  try { data = txt ? JSON.parse(txt) : null; } catch { /* non-JSON error body */ }
  if (!res.ok) throw new Error((data as { detail?: string } | null)?.detail || `HTTP ${res.status}`);
  return data as T;
}

/** SQLite "YYYY-MM-DD HH:MM:SS" (UTC) atau ISO → Date. */
function toDate(s: string) {
  return new Date(s.includes("T") ? s : s.replace(" ", "T") + "Z");
}

/** Waktu WIB ringkas: "04/10/26 16.26". */
export function fmtTime(s?: string) {
  if (!s) return "—";
  const d = toDate(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString("id-ID", { dateStyle: "short", timeStyle: "short", timeZone: "Asia/Jakarta" });
}

/** Waktu relatif: "baru saja", "5 mnt lalu", "3 jam lalu", "2 hari lalu". */
export function relTime(s?: string) {
  if (!s) return "—";
  const d = toDate(s);
  if (isNaN(d.getTime())) return s;
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return "baru saja";
  if (sec < 3600) return `${Math.floor(sec / 60)} mnt lalu`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} jam lalu`;
  return `${Math.floor(sec / 86400)} hari lalu`;
}

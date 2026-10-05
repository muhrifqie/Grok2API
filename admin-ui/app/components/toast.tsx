"use client";

// Port dari Waroengkuy Admin (components/ui/toast.tsx): store module-level + useSyncExternalStore,
// jadi `toast()` bisa dipanggil dari mana saja tanpa context.
import { useSyncExternalStore } from "react";
import { CheckCircle2, Info, Loader2, X, XCircle, type LucideIcon } from "lucide-react";
import { cn } from "@/app/lib/utils";

type ToastType = "success" | "error" | "loading" | "info";
type ToastAction = { label: string; onClick: () => void };
type ToastOpts = { duration?: number; action?: ToastAction; id?: number };
type ToastItem = { id: number; type: ToastType; message: string; action?: ToastAction };

let items: ToastItem[] = [];
const listeners = new Set<() => void>();
const timers = new Map<number, ReturnType<typeof setTimeout>>();
let seq = 1;

const emit = () => { items = items.slice(); listeners.forEach((l) => l()); };
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
const snapshot = () => items;
const EMPTY: ToastItem[] = [];

function schedule(id: number, duration: number) {
  const prev = timers.get(id);
  if (prev) clearTimeout(prev);
  if (duration !== Infinity && duration > 0) timers.set(id, setTimeout(() => dismiss(id), duration));
}

function push(type: ToastType, message: string, opts?: ToastOpts): number {
  const id = opts?.id ?? seq++;
  const duration = opts?.duration ?? (type === "loading" ? Infinity : type === "error" ? 5000 : 3500);
  const item: ToastItem = { id, type, message, action: opts?.action };
  items = items.some((i) => i.id === id) ? items.map((i) => (i.id === id ? item : i)) : [...items, item];
  emit();
  schedule(id, duration);
  return id;
}

export function dismiss(id: number) {
  const t = timers.get(id);
  if (t) { clearTimeout(t); timers.delete(id); }
  items = items.filter((i) => i.id !== id);
  emit();
}

/** toast("pesan") · toast.success/error/loading/info · loading → update dgn { id }. */
export const toast = Object.assign((message: string, opts?: ToastOpts) => push("info", message, opts), {
  success: (m: string, o?: ToastOpts) => push("success", m, o),
  error: (m: string, o?: ToastOpts) => push("error", m, o),
  loading: (m: string, o?: ToastOpts) => push("loading", m, o),
  info: (m: string, o?: ToastOpts) => push("info", m, o),
  dismiss,
});

const ICON: Record<ToastType, LucideIcon> = { success: CheckCircle2, error: XCircle, loading: Loader2, info: Info };
const TONE: Record<ToastType, string> = { success: "text-success", error: "text-danger", loading: "text-primary", info: "text-info" };

export function Toaster() {
  const toasts = useSyncExternalStore(subscribe, snapshot, () => EMPTY);
  if (!toasts.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-[100] flex flex-col items-center gap-2 px-4 sm:bottom-4 lg:inset-x-auto lg:right-4 lg:items-end">
      {toasts.map((t) => {
        const Icon = ICON[t.type];
        return (
          <div key={t.id} role="status" aria-live="polite"
            className="toast-in pointer-events-auto flex w-full max-w-sm items-center gap-3 border border-border-subtle bg-surface px-3.5 py-2.5 shadow-[var(--shadow-elevated)]">
            <Icon className={cn("size-4 shrink-0", TONE[t.type], t.type === "loading" && "animate-spin")} />
            <span className="min-w-0 flex-1 text-sm font-medium text-text-main">{t.message}</span>
            {t.action && (
              <button type="button" onClick={() => { t.action!.onClick(); dismiss(t.id); }}
                className="shrink-0 px-2 py-1 text-[13px] font-semibold text-primary transition-colors hover:bg-primary/10">
                {t.action.label}
              </button>
            )}
            <button type="button" onClick={() => dismiss(t.id)} aria-label="Tutup" className="shrink-0 text-text-subtle transition-colors hover:text-text-main">
              <X className="size-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

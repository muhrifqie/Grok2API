"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Activity, ChevronLeft, ChevronRight, CircleCheck, Loader2, LogIn, ShieldCheck, Trash2, Upload, UserPlus, UserX, Users, X,
} from "lucide-react";
import { api, fmtTime, relTime } from "@/app/lib/api";
import { fetchAccounts, fmtWindow, loginBusy, poolRes, quotaRes, statsRes, useRes, type Account, type AccountsPage as APage } from "@/app/lib/store";
import { cn, fmt } from "@/app/lib/utils";
import { toast } from "@/app/components/toast";
import { BulkLogin } from "./bulk";
import {
  Chip, Empty, ErrorBox, Field, FilterChip, Meter, Modal, PageHead, RefreshBtn, SearchBox, Section, Skel, SortTh, Stat, ctl, tbl, type SortState,
} from "@/app/components/ui";

type SortKey = "email" | "valid" | "request_count" | "error_count" | "last_used";
const STATUS = [
  { v: "all", label: "Semua" },
  { v: "valid", label: "Valid" },
  { v: "invalid", label: "Invalid", tone: "danger" as const },
  { v: "failed", label: "Login gagal", tone: "danger" as const },
  { v: "invalid_pw", label: "Pw salah", tone: "warning" as const },
  { v: "busy", label: "Sedang login", tone: "warning" as const },
];
const PAGE_SIZES = [25, 50, 100, 200];

function LoginChip({ s }: { s: string }) {
  if (!s || s === "idle") return <Chip>Idle</Chip>;
  if (s === "running") return <Chip tone="info"><Loader2 className="size-3 animate-spin" /> Login…</Chip>;
  if (s === "queued") return <Chip tone="warning">Antre</Chip>;
  if (s === "success") return <Chip tone="success">Berhasil</Chip>;
  if (s === "failed") return <Chip tone="danger">Gagal</Chip>;
  if (s === "invalid") return <Chip tone="warning">Pw salah</Chip>;
  return <Chip tone="danger" title={s}>Error</Chip>;
}

function useDebounced<T>(value: T, ms: number): T {
  const [d, setD] = useState(value);
  useEffect(() => { const t = setTimeout(() => setD(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return d;
}

function AccountsView() {
  const sp = useSearchParams();
  const router = useRouter();
  const quota = useRes(quotaRes).data?.accounts ?? [];
  const lim = (a: Account) => quota.find((x) => x.email === a.email)?.models?.["grok-3"];

  const [q, setQ] = useState(sp.get("q") ?? "");
  const [status, setStatus] = useState(sp.get("f") || "all");
  const [sort, setSort] = useState<SortState<SortKey>>({ key: "email", dir: "asc" });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [data, setData] = useState<APage | null>(null);
  const [err, setErr] = useState("");
  const [validating, setValidating] = useState(false);
  const [tick, setTick] = useState(0);
  const dq = useDebounced(q.trim(), 350);

  const [modal, setModal] = useState<"" | "add" | "bulk">(sp.get("add") ? "add" : sp.get("bulk") ? "bulk" : "");
  const [del, setDel] = useState<Account | null>(null);
  const [pending, setPending] = useState<Record<number, "check" | "login" | "del">>({});
  const [form, setForm] = useState({ email: "", password: "", bulk: "" });
  const [saving, setSaving] = useState(false);
  const [bulkBusy, setBulkBusy] = useState<"" | "check" | "login">("");

  // Ikuti query dari header search / pintasan Beranda.
  useEffect(() => {
    setQ(sp.get("q") ?? "");
    setStatus(sp.get("f") || "all");
    if (sp.get("add")) setModal("add");
    else if (sp.get("bulk")) setModal("bulk");
  }, [sp]);

  // Fetch satu halaman (server-side filter/sort/paginate). Data lama tetap tampil saat polling.
  // sigRef: saat filter/cari/urutan/ukuran berubah sementara bukan di halaman 1, balik ke hal 1 dulu
  // lalu fetch SEKALI di sana (hindari fetch ganda: stale-page + page-1).
  const sigRef = useRef("");
  useEffect(() => {
    const sig = `${dq}|${status}|${sort.key}|${sort.dir}|${pageSize}`;
    const changed = sig !== sigRef.current;
    sigRef.current = sig;
    if (changed && page !== 1) { setPage(1); return; }
    let cancel = false;
    setValidating(true);
    fetchAccounts({ page, page_size: pageSize, q: dq, status, sort: sort.key, dir: sort.dir })
      .then((d) => { if (!cancel) { setData(d); setErr(""); } })
      .catch((e) => { if (!cancel) setErr((e as Error).message); })
      .finally(() => { if (!cancel) setValidating(false); });
    return () => { cancel = true; };
  }, [page, pageSize, dq, status, sort.key, sort.dir, tick]);

  const rows = data?.rows ?? [];
  const counts = data?.counts;
  const anyBusy = (counts?.busy ?? 0) > 0;
  // Ada akun login → polling cepat (3s), selain itu 10s.
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), anyBusy ? 3000 : 10000);
    return () => clearInterval(t);
  }, [anyBusy]);

  const reload = () => { setTick((x) => x + 1); void statsRes.refresh(); void poolRes.refresh(); };
  const onSort = (k: SortKey) => setSort((s) => (s.key === k ? { key: k, dir: s.dir === "asc" ? "desc" : "asc" } : { key: k, dir: k === "email" ? "asc" : "desc" }));
  const filtered = !!dq || status !== "all";
  const resetFilter = () => { setQ(""); setStatus("all"); if (sp.toString()) router.replace("/accounts"); };
  const closeModal = () => { setModal(""); if (sp.get("add") || sp.get("bulk")) router.replace("/accounts"); };

  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total ? (page - 1) * pageSize + 1 : 0;
  const to = Math.min(page * pageSize, total);
  // Saat total menyusut (mis. hapus baris terakhir di halaman terakhir), tarik page balik ke rentang valid.
  useEffect(() => { if (data && page > pages) setPage(pages); }, [data, page, pages]);

  async function rowAct(a: Account, kind: "check" | "login" | "del") {
    setPending((p) => ({ ...p, [a.id]: kind }));
    try {
      if (kind === "check") {
        const r = await api<{ valid: boolean }>(`/admin/api/accounts/${a.id}/check`, { method: "POST" });
        if (r.valid) toast.success(`Cookie ${a.email} valid`);
        else toast.error(`Cookie ${a.email} invalid — perlu login ulang`, { action: { label: "Login", onClick: () => rowAct(a, "login") } });
      } else if (kind === "login") {
        const r = await api<{ status: string }>(`/admin/api/accounts/${a.id}/login`, { method: "POST" });
        toast.info(r.status === "already_running" ? "Login akun ini masih berjalan" : `Login ${a.email} dimulai (Camoufox headless)…`);
      } else {
        await api(`/admin/api/accounts/${a.id}`, { method: "DELETE" });
        toast.success(`${a.email} dihapus`);
        setDel(null);
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPending((p) => { const n = { ...p }; delete n[a.id]; return n; });
      reload();
    }
  }

  async function checkAll() {
    setBulkBusy("check");
    const id = toast.loading("Mengecek semua cookie akun valid…");
    try {
      const r = await api<{ valid: boolean }[]>("/admin/api/accounts/check-all", { method: "POST" });
      const ok = r.filter((x) => x.valid).length;
      toast.success(`Selesai: ${ok}/${r.length} cookie valid`, { id });
    } catch (e) { toast.error((e as Error).message, { id }); }
    finally { setBulkBusy(""); reload(); }
  }

  async function addAccount() {
    if (!form.email.trim() || !form.password.trim()) return toast.error("Email & password wajib diisi");
    setSaving(true);
    try {
      await api("/admin/api/accounts", { method: "POST", body: JSON.stringify({ email: form.email.trim(), password: form.password }) });
      toast.success(`${form.email} ditambahkan`, { action: { label: "Lihat invalid", onClick: () => setStatus("invalid") } });
      setForm({ ...form, email: "", password: "" });
      closeModal();
    } catch (e) { toast.error((e as Error).message); }
    finally { setSaving(false); reload(); }
  }

  const bulkLines = form.bulk.split("\n").filter((l) => l.trim()).length;
  async function bulkImport() {
    if (!bulkLines) return toast.error("Tempel minimal satu baris email:password");
    setSaving(true);
    try {
      const r = await api<{ added: number; already_in_db: number; duplicate_in_file: number; invalid_lines: number }>("/admin/api/accounts/bulk", { method: "POST", body: JSON.stringify({ data: form.bulk }) });
      toast.success(`${r.added} ditambah · ${r.already_in_db} sudah ada · ${r.duplicate_in_file} dup · ${r.invalid_lines} invalid`);
      setForm({ ...form, bulk: "" });
      closeModal();
    } catch (e) { toast.error((e as Error).message); }
    finally { setSaving(false); reload(); }
  }

  return (
    <div className="space-y-5">
      <PageHead
        icon={Users}
        eyebrow="Pool akun"
        title="Akun Grok"
        desc="Akun valid dipakai bergiliran (round-robin) untuk melayani request API. Cookie kedaluwarsa → login ulang via Camoufox."
        actions={
          <>
            <RefreshBtn onClick={reload} busy={validating} />
            <button type="button" onClick={checkAll} disabled={!!bulkBusy || !(counts?.valid)} className={ctl.btn}>
              {bulkBusy === "check" ? <Loader2 className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />} Cek cookie
            </button>
            <button type="button" onClick={() => setModal("bulk")} className={ctl.btn}><Upload className="size-4" /> Bulk import</button>
            <button type="button" onClick={() => setModal("add")} className={ctl.primary}><UserPlus className="size-4" /> Tambah akun</button>
          </>
        }
      />

      {/* KPI — dari counts server (bukan hitung di klien) */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {!data ? Array.from({ length: 4 }, (_, i) => <Skel key={i} className="h-[92px]" />) : (
          <>
            <Stat label="Total akun" icon={Users} tone="primary" value={fmt(counts!.all)} hint={`${fmt(data.ever_login)} pernah login`} />
            <Stat label="Valid" icon={CircleCheck} tone="success" value={fmt(counts!.valid)} hint="siap melayani request" />
            <Stat label="Invalid" icon={UserX} tone={counts!.invalid ? "danger" : "muted"} dim={!counts!.invalid} value={fmt(counts!.invalid)} hint="perlu login ulang" />
            <Stat label="Total request" icon={Activity} tone="info" value={fmt(data.req_sum)} hint={`${fmt(data.err_sum)} error`} />
          </>
        )}
      </div>

      <BulkLogin />

      {/* Filter */}
      <div className="space-y-2.5 border border-border-subtle bg-surface p-3">
        <SearchBox value={q} onChange={setQ} placeholder="Cari email akun…" />
        <div className="flex flex-wrap items-center gap-1.5">
          {STATUS.map((s, i) => (
            <span key={s.v} className="contents">
              {i === 1 && <span aria-hidden className="mx-0.5 h-5 w-px bg-border-subtle" />}
              <FilterChip on={status === s.v} onClick={() => setStatus(s.v)} tone={s.tone}
                count={counts ? (s.v === "all" ? counts.all : counts[s.v as keyof typeof counts]) : undefined}>{s.label}</FilterChip>
            </span>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted">
          <span>{data ? <>Menampilkan <b className="font-semibold tabular-nums text-text-main">{fmt(from)}–{fmt(to)}</b> dari {fmt(total)} akun</> : "Memuat…"}</span>
          {filtered && <button type="button" onClick={resetFilter} className="inline-flex items-center gap-1 font-semibold text-primary hover:underline"><X className="size-3.5" /> Reset filter</button>}
        </div>
      </div>

      {/* Tabel */}
      {!data ? (
        err ? <Section><ErrorBox msg={err} onRetry={reload} busy={validating} /></Section>
          : <Section flush><div className="space-y-px p-1">{Array.from({ length: 8 }, (_, i) => <Skel key={i} className="h-12" />)}</div></Section>
      ) : !rows.length ? (
        <Section>
          <Empty emoji={counts!.all ? "🔍" : "🫙"} title={counts!.all ? "Tidak ada yang cocok" : "Belum ada akun"}
            action={counts!.all
              ? <button type="button" onClick={resetFilter} className={ctl.btn}><X className="size-4" /> Reset filter</button>
              : <button type="button" onClick={() => setModal("add")} className={ctl.primary}><UserPlus className="size-4" /> Tambah akun</button>}>
            {counts!.all ? "Tidak ada akun yang cocok dengan filter." : "Tambah akun Grok satu per satu atau lewat bulk import."}
          </Empty>
        </Section>
      ) : (
        <Section flush>
          <div className={cn(tbl.wrap, validating && "opacity-60 transition-opacity")}>
            <table className={cn(tbl.table, "min-w-[980px]")}>
              <thead>
                <tr>
                  <SortTh k="email" sort={sort} onSort={onSort}>Akun</SortTh>
                  <SortTh k="valid" sort={sort} onSort={onSort}>Cookie</SortTh>
                  <th className={tbl.th}>Login</th>
                  <th className={tbl.th}>Kuota grok-3</th>
                  <SortTh k="request_count" sort={sort} onSort={onSort} num>Request</SortTh>
                  <SortTh k="error_count" sort={sort} onSort={onSort} num>Error</SortTh>
                  <SortTh k="last_used" sort={sort} onSort={onSort}>Terakhir dipakai</SortTh>
                  <th className={cn(tbl.th, "text-right")}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => {
                  const p = pending[a.id];
                  const l = lim(a);
                  return (
                    <tr key={a.id} className={tbl.row}>
                      <td className={tbl.td}>
                        <div className="flex min-w-0 items-center gap-3">
                          <span aria-hidden className="grid size-8 shrink-0 place-items-center rounded-full bg-primary/10 text-xs font-bold uppercase text-primary">{a.email.charAt(0)}</span>
                          <div className="min-w-0">
                            <p className="truncate font-mono text-[13px] font-medium text-text-main">{a.email}</p>
                            <p className="mt-0.5 truncate text-[11px] text-text-subtle">{a.user_id ? `uid ${a.user_id.slice(0, 8)}…` : "belum pernah login"} · dicek {relTime(a.last_verified)}</p>
                          </div>
                        </div>
                      </td>
                      <td className={tbl.td}>{a.valid ? <Chip tone="success">Valid</Chip> : <Chip tone="danger">Invalid</Chip>}</td>
                      <td className={tbl.td}><LoginChip s={a.login_status} /></td>
                      <td className={tbl.td}>{l ? (
                        <div className="w-28 space-y-1" title={`Jendela ${fmtWindow(l.window)}`}>
                          <p className="text-[13px] tabular-nums"><b className={cn("font-semibold", l.remaining === 0 && "text-danger")}>{fmt(l.remaining)}</b><span className="text-text-subtle">/{fmt(l.total)}</span></p>
                          <Meter value={l.remaining / l.total} tone={l.remaining === 0 ? "danger" : l.remaining / l.total < 0.25 ? "warning" : "success"} />
                        </div>) : <span className="text-xs text-text-subtle">—</span>}</td>
                      <td className={cn(tbl.td, tbl.num, "font-semibold")}>{fmt(a.request_count)}</td>
                      <td className={cn(tbl.td, tbl.num, a.error_count > 0 ? "font-semibold text-danger" : "text-text-muted")}>{fmt(a.error_count)}</td>
                      <td className={cn(tbl.td, "whitespace-nowrap text-xs text-text-muted")} title={fmtTime(a.last_used)}>{relTime(a.last_used)}</td>
                      <td className={tbl.td}>
                        <div className="flex justify-end gap-1.5">
                          <button type="button" onClick={() => rowAct(a, "check")} disabled={!!p} className={ctl.icon} title="Cek cookie" aria-label={`Cek cookie ${a.email}`}>
                            {p === "check" ? <Loader2 className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />}
                          </button>
                          <button type="button" onClick={() => rowAct(a, "login")} disabled={!!p || loginBusy(a.login_status)} className={ctl.icon} title="Login ulang (Camoufox)" aria-label={`Login ${a.email}`}>
                            {p === "login" ? <Loader2 className="size-4 animate-spin" /> : <LogIn className="size-4" />}
                          </button>
                          <button type="button" onClick={() => setDel(a)} disabled={!!p} className={cn(ctl.icon, "hover:border-danger/40 hover:text-danger")} title="Hapus akun" aria-label={`Hapus ${a.email}`}>
                            <Trash2 className="size-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {pages > 1 && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle px-3 py-2.5 text-[13px]">
              <div className="flex items-center gap-2 text-text-muted">
                <span>Baris:</span>
                <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} className="border border-border bg-surface px-2 py-1 text-[13px] outline-none focus:border-primary/50">
                  {PAGE_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div className="flex items-center gap-1.5">
                <button type="button" onClick={() => setPage(1)} disabled={page <= 1} className={cn(ctl.btn, "px-2")}>«</button>
                <button type="button" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} className={cn(ctl.btn, "px-2")}><ChevronLeft className="size-4" /></button>
                <span className="px-2 tabular-nums text-text-muted">Hal <b className="font-semibold text-text-main">{fmt(page)}</b> / {fmt(pages)}</span>
                <button type="button" onClick={() => setPage((p) => Math.min(pages, p + 1))} disabled={page >= pages} className={cn(ctl.btn, "px-2")}><ChevronRight className="size-4" /></button>
                <button type="button" onClick={() => setPage(pages)} disabled={page >= pages} className={cn(ctl.btn, "px-2")}>»</button>
              </div>
            </div>
          )}
        </Section>
      )}

      {modal === "add" && (
        <Modal title="Tambah akun" desc="Akun baru masuk sebagai invalid sampai berhasil login." icon={UserPlus} onClose={closeModal}
          footer={<>
            <button type="button" onClick={closeModal} className={ctl.btn}>Batal</button>
            <button type="button" onClick={addAccount} disabled={saving} className={ctl.primary}>{saving && <Loader2 className="size-4 animate-spin" />} Tambah</button>
          </>}>
          <Field label="Email">
            <input autoFocus type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="nama@domain.com" className={cn(ctl.input, "w-full")} />
          </Field>
          <Field label="Password">
            <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && addAccount()} placeholder="••••••••" className={cn(ctl.input, "w-full")} />
          </Field>
        </Modal>
      )}

      {modal === "bulk" && (
        <Modal title="Bulk import" desc="Satu akun per baris. Pemisah: | : , ; tab atau spasi. Duplikat otomatis dibuang." icon={Upload} onClose={closeModal}
          footer={<>
            <button type="button" onClick={closeModal} className={ctl.btn}>Batal</button>
            <button type="button" onClick={bulkImport} disabled={saving || !bulkLines} className={ctl.primary}>
              {saving && <Loader2 className="size-4 animate-spin" />} Import {bulkLines ? `${fmt(bulkLines)} baris` : ""}
            </button>
          </>}>
          <Field label="Daftar akun" hint="Email yang sudah ada otomatis dilewati.">
            <textarea autoFocus rows={9} value={form.bulk} onChange={(e) => setForm({ ...form, bulk: e.target.value })}
              placeholder={"email1@domain.com:password1\nemail2@domain.com:password2"}
              className={cn(ctl.input, "h-auto w-full resize-y py-2 font-mono text-[13px]")} />
          </Field>
        </Modal>
      )}

      {del && (
        <Modal title="Hapus akun?" icon={Trash2} onClose={() => setDel(null)}
          footer={<>
            <button type="button" onClick={() => setDel(null)} className={ctl.btn}>Batal</button>
            <button type="button" onClick={() => rowAct(del, "del")} disabled={!!pending[del.id]} className={ctl.danger}>
              {pending[del.id] ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />} Hapus
            </button>
          </>}>
          <p className="text-sm text-text-muted">
            <b className="font-mono font-semibold text-text-main">{del.email}</b> akan dihapus dari pool beserta cookie-nya. Riwayat log tetap tersimpan.
          </p>
        </Modal>
      )}
    </div>
  );
}

export default function AccountsPage() {
  return <Suspense><AccountsView /></Suspense>;
}

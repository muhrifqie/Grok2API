"use client";

/**
 * Render jawaban model sebagai Markdown (GFM: tabel, daftar tugas, coret). Blok kode = label bahasa + tombol salin.
 * ponytail: tanpa syntax highlighter (shiki/prism ±200 KB) — tambah kalau perlu warna kode.
 * Tautan selalu tab baru + rel aman; HTML mentah dari model TIDAK dirender (default react-markdown).
 */
import { memo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setDone(true); setTimeout(() => setDone(false), 1500); } catch { /* izin clipboard ditolak */ }
  };
  return (
    <div className="md-code group">
      <div className="flex items-center justify-between border-b border-border-subtle bg-surface-2 px-3 py-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wider text-text-subtle">{lang || "teks"}</span>
        <button type="button" onClick={copy} className="inline-flex items-center gap-1 text-[11px] font-semibold text-text-muted transition-colors hover:text-text-main">
          {done ? <><Check className="size-3.5" /> Disalin</> : <><Copy className="size-3.5" /> Salin</>}
        </button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  );
}

const components: Components = {
  pre: ({ children }) => <>{children}</>,
  code({ className, children }) {
    const text = String(children ?? "");
    const lang = /language-([\w+-]+)/.exec(className || "")?.[1] ?? "";
    // Blok kode = punya kelas bahasa ATAU berisi baris baru; selain itu kode inline.
    if (lang || text.includes("\n")) return <CodeBlock lang={lang} code={text.replace(/\n$/, "")} />;
    return <code className="md-inline">{children}</code>;
  },
  a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer nofollow">{children}</a>,
  table: ({ children }) => <div className="md-table"><table>{children}</table></div>,
};

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{text}</ReactMarkdown>
    </div>
  );
});

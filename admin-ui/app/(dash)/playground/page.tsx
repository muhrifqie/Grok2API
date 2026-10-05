"use client";

import { useCallback, useEffect, useState } from "react";
import { ImageIcon, MessageSquare, Sparkles } from "lucide-react";
import { api } from "@/app/lib/api";
import { keysRes, useRes } from "@/app/lib/store";
import { cn } from "@/app/lib/utils";
import { PageHead, ctl } from "@/app/components/ui";
import { ChatTab } from "./chat";
import { ImagineTab } from "./imagine";

const mask = (k: string) => (k.length > 16 ? `${k.slice(0, 9)}••••${k.slice(-4)}` : k);

export default function Playground() {
  const keys = useRes(keysRes);
  const active = (keys.data ?? []).filter((k) => k.active);
  const [keyPick, setKeyPick] = useState("");
  const keyVal = keyPick || active[0]?.key || "";
  const [tab, setTab] = useState<"chat" | "imagine">("chat");
  const [models, setModels] = useState<string[]>(["grok-auto"]);
  const [pending, setPending] = useState<string[]>([]);
  const clearPending = useCallback(() => setPending([]), []);

  useEffect(() => {
    api<{ data: { id: string }[] }>("/v1/models").then((d) => setModels(d.data.map((m) => m.id))).catch(() => {});
  }, []);

  const tabs = [
    { v: "chat" as const, label: "Chat", icon: MessageSquare },
    { v: "imagine" as const, label: "Imagine", icon: ImageIcon },
  ];

  return (
    <div className="space-y-5">
      <PageHead icon={Sparkles} eyebrow="Uji coba" title="Playground"
        desc="Coba endpoint API langsung: chat (teks + gambar) lewat /v1/chat/completions dan buat gambar lewat /v1/images/generations."
        actions={
          <>
            <select value={keyVal} onChange={(e) => setKeyPick(e.target.value)} disabled={!active.length} aria-label="API key" title="API key yang dipakai"
              className={cn(ctl.input, "max-w-[15rem] font-mono text-[13px]")}>
              {!active.length && <option value="">Belum ada key aktif</option>}
              {active.map((k) => <option key={k.id} value={k.key}>{k.name} · {mask(k.key)}</option>)}
            </select>
            <div role="tablist" aria-label="Mode playground" className="flex border border-border">
              {tabs.map(({ v, label, icon: Icon }) => (
                <button key={v} role="tab" type="button" aria-selected={tab === v} onClick={() => setTab(v)}
                  className={cn("inline-flex h-9 items-center gap-1.5 px-3.5 text-[13px] font-semibold transition-colors",
                    tab === v ? "bg-primary text-primary-foreground" : "bg-surface text-text-muted hover:text-text-main")}>
                  <Icon className="size-4" /> {label}
                </button>
              ))}
            </div>
          </>
        } />

      {/* Dua tab tetap ter-mount → riwayat chat & galeri tidak hilang saat pindah tab. */}
      <div hidden={tab !== "chat"}><ChatTab keyVal={keyVal} models={models} pending={pending} onPendingUsed={clearPending} /></div>
      <div hidden={tab !== "imagine"}><ImagineTab keyVal={keyVal} onUseInChat={(urls) => { setPending(urls); setTab("chat"); }} /></div>
    </div>
  );
}

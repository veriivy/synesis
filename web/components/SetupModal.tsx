"use client";

import { useState } from "react";
import type { Provider } from "@/lib/types";
import { PROVIDER_COLOR, PROVIDER_LABEL, ProviderDot } from "@/components/ui";

export interface LocalIdentity {
  display_name: string;
  provider: Provider;
  model: string;
  /** Held in memory for the room's lifetime only. Never persisted, never logged. */
  api_key?: string;
}

export const DEFAULT_MODEL: Record<Provider, string> = {
  claude: "claude-opus-5",
  gpt: "gpt-5",
  gemini: "gemini-2.5-pro",
};

const PROVIDERS: Provider[] = ["claude", "gpt", "gemini"];

export function SetupModal({ onJoin }: { onJoin: (identity: LocalIdentity) => void }) {
  const [name, setName] = useState("");
  const [provider, setProvider] = useState<Provider>("claude");
  const [model, setModel] = useState(DEFAULT_MODEL.claude);
  const [apiKey, setApiKey] = useState("");

  function pickProvider(p: Provider) {
    setProvider(p);
    setModel(DEFAULT_MODEL[p]);
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    onJoin({
      display_name: name.trim() || "You",
      provider,
      model: model.trim() || DEFAULT_MODEL[provider],
      api_key: apiKey.trim() || undefined,
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-md rounded border border-ide-border bg-ide-panel shadow-2xl"
      >
        <header className="border-b border-ide-border px-4 py-3">
          <h1 className="font-mono text-[12px] tracking-widest text-ide-text uppercase">
            join room
          </h1>
          <p className="mt-1 text-[12px] text-ide-dim">
            Your agent argues for you. Pick who it is.
          </p>
        </header>

        <div className="grid gap-4 p-4">
          <label className="grid gap-1.5">
            <span className="font-mono text-[10px] tracking-wider text-ide-faint uppercase">
              display name
            </span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              placeholder="Audrey"
              className="rounded-sm border border-ide-border bg-ide-bg px-2.5 py-1.5 text-[13px] text-ide-text outline-none focus:border-ide-accent"
            />
          </label>

          <div className="grid gap-1.5">
            <span className="font-mono text-[10px] tracking-wider text-ide-faint uppercase">
              provider
            </span>
            <div className="grid grid-cols-3 gap-1.5">
              {PROVIDERS.map((p) => {
                const active = p === provider;
                return (
                  <button
                    key={p}
                    type="button"
                    onClick={() => pickProvider(p)}
                    className={`flex items-center justify-center gap-1.5 rounded-sm border px-2 py-1.5 text-[12px] ${
                      active ? "bg-ide-active text-ide-text" : "text-ide-dim hover:bg-ide-hover"
                    }`}
                    style={{
                      borderColor: active ? PROVIDER_COLOR[p] : "var(--color-ide-border)",
                    }}
                  >
                    <ProviderDot provider={p} />
                    {PROVIDER_LABEL[p]}
                  </button>
                );
              })}
            </div>
          </div>

          <label className="grid gap-1.5">
            <span className="font-mono text-[10px] tracking-wider text-ide-faint uppercase">
              model
            </span>
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="rounded-sm border border-ide-border bg-ide-bg px-2.5 py-1.5 font-mono text-[12px] text-ide-text outline-none focus:border-ide-accent"
            />
          </label>

          <label className="grid gap-1.5">
            <span className="font-mono text-[10px] tracking-wider text-ide-faint uppercase">
              api key · optional
            </span>
            <input
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              type="password"
              placeholder="leave blank to use the server key"
              className="rounded-sm border border-ide-border bg-ide-bg px-2.5 py-1.5 font-mono text-[12px] text-ide-text outline-none focus:border-ide-accent"
            />
            <span className="text-[11px] text-ide-faint">
              Held in memory for this room only. Never written to disk, never logged.
            </span>
          </label>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-ide-border px-4 py-3">
          <button
            type="submit"
            className="rounded-sm px-3 py-1.5 text-[12px] font-medium text-white"
            style={{ background: "var(--color-ide-accent)" }}
          >
            Enter room
          </button>
        </footer>
      </form>
    </div>
  );
}

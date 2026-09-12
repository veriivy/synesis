"use client";

import type { RoomState, RoomStatus } from "@/lib/roomReducer";
import { PROVIDER_LABEL, ProviderDot } from "@/components/ui";

const STATUS_LABEL: Record<RoomStatus, string> = {
  setup: "setup",
  tasks: "tasks",
  negotiating: "negotiating",
  awaiting_approval: "awaiting approval",
  executing: "executing",
  done: "done",
};

function Control({
  children,
  onClick,
  title,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className="rounded-sm border border-ide-border px-2 py-[3px] font-mono text-[11px] text-ide-dim hover:bg-ide-hover disabled:opacity-30"
    >
      {children}
    </button>
  );
}

export interface Playback {
  playing: boolean;
  index: number;
  total: number;
  speed: number;
  toggle: () => void;
  step: () => void;
  restart: () => void;
  setSpeed: (s: number) => void;
}

export function TopBar({ state, playback }: { state: RoomState; playback: Playback }) {

  return (
    <header className="flex h-10 shrink-0 items-center gap-3 border-b border-ide-border bg-ide-rail px-3">
      <span className="font-mono text-[12px] tracking-widest text-ide-text uppercase">synesis</span>
      <span className="font-mono text-[11px] text-ide-faint">
        {state.roomId || "no room"}
      </span>
      <span className="rounded-sm border border-ide-border px-1.5 py-px font-mono text-[10px] text-ide-dim uppercase">
        {STATUS_LABEL[state.status]}
      </span>

      <div className="ml-2 flex items-center gap-1.5">
        {state.participants.map((p) => (
          <span
            key={p.user_id}
            title={p.model ? `${p.user_id} · ${p.model}` : p.user_id}
            className="flex items-center gap-1.5 rounded-sm border border-ide-border bg-ide-panel px-2 py-[3px] text-[11px] text-ide-dim"
          >
            <ProviderDot provider={p.provider} />
            <span className="text-ide-text">{p.display_name}</span>
            {p.provider && <span className="text-ide-faint">{PROVIDER_LABEL[p.provider]}</span>}
            {p.is_local && <span className="font-mono text-[10px] text-ide-faint">you</span>}
          </span>
        ))}
        {state.participants.length === 0 && (
          <span className="text-[11px] text-ide-faint">no participants yet</span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-1.5">
        <span className="font-mono text-[10px] text-ide-faint">
          {playback.index}/{playback.total}
        </span>
        <Control title="Restart the stream" onClick={playback.restart}>
          ⟲
        </Control>
        <Control
          title={playback.playing ? "Pause" : "Play"}
          onClick={playback.toggle}
          disabled={playback.index >= playback.total}
        >
          {playback.playing ? "❚❚" : "▶"}
        </Control>
        <Control
          title="Next event"
          onClick={playback.step}
          disabled={playback.index >= playback.total}
        >
          ▶❚
        </Control>
        <select
          value={playback.speed}
          onChange={(e) => playback.setSpeed(Number(e.target.value))}
          title="Playback speed"
          className="rounded-sm border border-ide-border bg-ide-panel px-1 py-[3px] font-mono text-[11px] text-ide-dim"
        >
          <option value={0.5}>0.5×</option>
          <option value={1}>1×</option>
          <option value={2}>2×</option>
          <option value={4}>4×</option>
        </select>
      </div>
    </header>
  );
}

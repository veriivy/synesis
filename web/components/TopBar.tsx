"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { RoomState, RoomStatus } from "@/lib/roomReducer";
import type { FixturePlayer } from "@/lib/useFixturePlayer";
import { PROVIDER_LABEL, ProviderDot } from "@/components/ui";

const STATUS_LABEL: Record<RoomStatus, string> = {
  setup: "setup",
  tasks: "tasks",
  negotiating: "negotiating",
  awaiting_approval: "awaiting approval",
  executing: "executing",
  done: "done",
};

export function Control({
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

/** Play / pause / step / reset, shared by the top bar and the dev inspector. */
export function TransportControls({ player }: { player: FixturePlayer }) {
  return (
    <>
      <span className="font-mono text-[10px] text-ide-faint">
        {player.index}/{player.total}
      </span>
      <Control title="Reset to the first event" onClick={player.reset}>
        ⟲
      </Control>
      <Control
        title={player.playing ? "Pause" : "Play"}
        onClick={player.toggle}
        disabled={player.atEnd}
      >
        {player.playing ? "❚❚" : "▶"}
      </Control>
      <Control title="Step one event" onClick={player.step} disabled={player.atEnd}>
        ▶❚
      </Control>
      <select
        value={player.speed}
        onChange={(e) => player.setSpeed(Number(e.target.value))}
        title="Playback speed"
        className="rounded-sm border border-ide-border bg-ide-panel px-1 py-[3px] font-mono text-[11px] text-ide-dim"
      >
        <option value={0.5}>0.5×</option>
        <option value={1}>1×</option>
        <option value={2}>2×</option>
        <option value={4}>4×</option>
      </select>
    </>
  );
}

export function TopBar({ state, player }: { state: RoomState; player: FixturePlayer }) {
  const pathname = usePathname();
  const onDev = pathname === "/dev";

  return (
    <header className="flex h-10 shrink-0 items-center gap-3 border-b border-ide-border bg-ide-rail px-3">
      <span className="font-mono text-[12px] tracking-widest text-ide-text uppercase">synesis</span>
      <span className="font-mono text-[11px] text-ide-faint">{state.roomId || "no room"}</span>
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
        <Link
          href={onDev ? "/" : "/dev"}
          className="rounded-sm border border-ide-border px-2 py-[3px] font-mono text-[10px] text-ide-faint hover:bg-ide-hover hover:text-ide-dim"
          title={onDev ? "Back to the room" : "Fixture replay harness"}
        >
          {onDev ? "room" : "dev"}
        </Link>
        <TransportControls player={player} />
      </div>
    </header>
  );
}

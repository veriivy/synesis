"use client";

import { useEffect, useRef, useState } from "react";
import type { SSEEvent } from "@/lib/types";
import type { RoomState } from "@/lib/roomReducer";
import type { FixturePlayer } from "@/lib/useFixturePlayer";
import { TransportControls } from "@/components/TopBar";

/** A one-line summary of an event, so the list reads without expanding anything. */
function summarize(event: SSEEvent): string {
  switch (event.type) {
    case "participant_joined":
      return `${event.user_id} · ${event.provider}`;
    case "poa_generated":
      return `${event.agent_id} · ${event.poa.steps.length} steps`;
    case "analysis":
      return `round ${event.round} · ${event.differences.length} diffs${
        event.converged ? " · converged" : ""
      }`;
    case "agent_message":
      return `${event.agent_id} · ${event.addresses_issues.join(" ")}`;
    case "user_message":
      return event.user_id;
    case "moderator_message":
      return `round ${event.round}`;
    case "round_complete":
      return `round ${event.round}`;
    case "plan_proposed":
      return `${event.plan.plan_id} · ${event.plan.resolutions.length} resolutions`;
    case "approval_updated":
      return `${event.user_id} · ${event.approved ? "yes" : "no"}`;
    case "plan_approved":
      return event.plan_id;
    case "context_updated":
      return `v${event.version}`;
    case "tickets_created":
      return `${event.tickets.length} tickets`;
    case "ticket_started":
      return `${event.ticket_id} · ${event.agent_id}`;
    case "file_written":
      return `${event.accepted ? "✓" : "✕"} ${event.path}`;
    case "ticket_completed":
      return `${event.ticket_id} · ${event.status}`;
    case "error":
      return event.where;
  }
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-baseline justify-between gap-2 py-px">
      <span className="font-mono text-[10px] text-ide-faint">{label}</span>
      <span className="truncate font-mono text-[11px] text-ide-dim">{value}</span>
    </div>
  );
}

function StateSummary({ state }: { state: RoomState }) {
  const refused = state.writeLog.filter((w) => !w.accepted).length;
  return (
    <div className="border-t border-ide-border p-3">
      <div className="mb-1.5 font-mono text-[10px] tracking-wider text-ide-faint uppercase">
        state
      </div>
      <Stat label="status" value={state.status} />
      <Stat label="currentRound" value={state.currentRound} />
      <Stat label="participants" value={state.participants.map((p) => p.user_id).join(", ") || "—"} />
      <Stat
        label="latestAnalysis"
        value={
          state.latestAnalysis
            ? `r${state.latestAnalysis.round} · ${state.latestAnalysis.converged ? "converged" : "open"}`
            : "null"
        }
      />
      <Stat label="proposedPlan" value={state.proposedPlan?.status ?? "null"} />
      <Stat
        label="approvals"
        value={
          Object.entries(state.approvals)
            .map(([u, ok]) => `${u}:${ok ? "y" : "n"}`)
            .join(" ") || "—"
        }
      />
      <Stat label="tickets" value={state.tickets.length} />
      <Stat label="writeLog" value={`${state.writeLog.length} (${refused} refused)`} />
      <Stat label="messages" value={state.messages.length} />
    </div>
  );
}

/**
 * The development harness: every fixture event, which ones have been dispatched,
 * the raw JSON of the one in flight, and the state it produced. Click any row to
 * replay from the seed up to that point.
 */
export function EventInspector({ player }: { player: FixturePlayer }) {
  const [showJson, setShowJson] = useState(true);
  const list = useRef<HTMLDivElement>(null);

  // Keep the event about to fire in view while playing.
  useEffect(() => {
    list.current?.querySelector('[data-current="true"]')?.scrollIntoView({ block: "nearest" });
  }, [player.index]);

  const inFlight = player.lastEvent;

  return (
    <aside className="flex h-full flex-col border-l border-ide-border bg-ide-panel">
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-ide-border px-3">
        <span className="font-mono text-[10px] tracking-widest text-ide-faint uppercase">
          fixture replay
        </span>
        <span className="ml-auto font-mono text-[10px] text-ide-faint">
          {player.playing ? "playing" : player.atEnd ? "ended" : "paused"}
        </span>
      </header>

      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-ide-border p-2">
        <TransportControls player={player} />
      </div>

      <div ref={list} className="min-h-0 flex-1 overflow-auto">
        {player.total === 0 && <p className="p-3 text-[12px] text-ide-faint">No events.</p>}
        {player.events.map((event, i) => {
          const done = i < player.index;
          const current = i === player.index - 1;
          return (
            <button
              key={i}
              type="button"
              data-current={current}
              onClick={() => player.seekTo(i + 1)}
              title="Replay from the seed up to and including this event"
              className={`flex w-full items-baseline gap-2 px-2 py-1 text-left font-mono text-[11px] ${
                current ? "bg-ide-active" : "hover:bg-ide-hover"
              }`}
            >
              <span className="w-5 shrink-0 text-right text-ide-faint">{i + 1}</span>
              <span className={done ? "text-ide-text" : "text-ide-faint"}>{event.type}</span>
              <span className="ml-auto truncate text-ide-faint">{summarize(event)}</span>
            </button>
          );
        })}
      </div>

      <div className="shrink-0 border-t border-ide-border">
        <button
          type="button"
          onClick={() => setShowJson((v) => !v)}
          className="flex w-full items-center gap-2 px-3 py-2 text-left"
        >
          <span className="font-mono text-[10px] tracking-wider text-ide-faint uppercase">
            last event
          </span>
          <span className="font-mono text-[10px] text-ide-dim">{inFlight?.type ?? "—"}</span>
          <span className="ml-auto font-mono text-[10px] text-ide-faint">
            {showJson ? "hide" : "show"}
          </span>
        </button>
        {showJson && (
          <pre className="max-h-56 overflow-auto border-t border-ide-border bg-ide-bg p-2 font-mono text-[10px] leading-relaxed text-ide-dim">
            {inFlight ? JSON.stringify(inFlight, null, 2) : "nothing dispatched yet"}
          </pre>
        )}
      </div>

      <StateSummary state={player.state} />
    </aside>
  );
}

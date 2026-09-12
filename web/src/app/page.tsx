"use client";

/**
 * Three panes: the negotiation, the plan, the writes.
 *
 * The layout is the pitch. Left is the argument, middle is what they agreed and the
 * approval gate, right is code landing in owned files with rejections in red.
 *
 * The whole page is a client component — it holds the SSE connection and the event
 * log, so there is nothing here a server render could usefully produce.
 */

import { useMemo, useState } from "react";
import { approvePlan, createRoom, postIntent } from "@/lib/api";
import Intake from "@/components/Intake";
import PlanPanel from "@/components/PlanPanel";
import Transcript from "@/components/Transcript";
import WriteLog from "@/components/WriteLog";
import { useRoomStream } from "@/lib/useRoomStream";
import type { Phase, Requirement, UnresolvedIssue } from "@/lib/types";

type Participant = {
  user_id: string;
  agent_id: string;
  display_name: string;
  requirements: Requirement[];
};

const PHASE_COPY: Record<Phase, string> = {
  waiting: "waiting for requirements",
  negotiating: "agents are negotiating",
  awaiting_approval: "waiting for your approval",
  deadlocked: "deadlocked — your call",
  executing: "agents are writing code",
  done: "done",
  rejected: "plan rejected",
};

export default function Page() {
  const [roomId, setRoomId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { events, connection, plan } = useRoomStream(roomId);

  const deadlock = useMemo<UnresolvedIssue[]>(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const event = events[i];
      if (event.type === "deadlock") return event.unresolved;
    }
    return [];
  }, [events]);

  /** Derived from the stream rather than polled, so the header never lags the events. */
  const phase = useMemo<Phase>(() => {
    let current: Phase = roomId ? "negotiating" : "waiting";
    for (const event of events) {
      if (event.type === "plan_proposed") current = "awaiting_approval";
      else if (event.type === "deadlock") current = "deadlocked";
      else if (event.type === "file_written") current = "executing";
      else if (event.type === "plan_rejected") current = "rejected";
      else if (event.type === "commit") current = "executing";
    }
    if (plan?.status === "approved" && current === "awaiting_approval") current = "executing";
    return current;
  }, [events, plan, roomId]);

  const start = async (feature: string, participants: Participant[]) => {
    setBusy(true);
    setError(null);
    try {
      const { room_id } = await createRoom(feature);
      setRoomId(room_id);
      // Sequential, not parallel: the server starts the negotiation on the last intent,
      // and a race here can start it against a half-registered participant list.
      for (const participant of participants) {
        await postIntent(room_id, participant);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setRoomId(null);
    } finally {
      setBusy(false);
    }
  };

  const approve = async (approved: boolean, notes?: string) => {
    if (!roomId) return;
    setBusy(true);
    try {
      await approvePlan(roomId, approved, notes);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  if (!roomId) return <Intake onStart={start} busy={busy} error={error} />;

  const streamErrors = events.filter((e) => e.type === "error");

  return (
    <div className="flex h-screen flex-col">
      <header className="flex shrink-0 items-center gap-4 border-b border-slate-800 px-5 py-3">
        <h1 className="text-sm font-semibold tracking-tight text-slate-100">Synesis</h1>
        <span className="font-mono text-xs text-slate-600">{roomId}</span>
        <span className="text-xs text-slate-400">{PHASE_COPY[phase]}</span>
        <div className="flex-1" />
        <span
          className={`flex items-center gap-1.5 text-xs ${
            connection === "open"
              ? "text-emerald-400"
              : connection === "error"
                ? "text-rose-400"
                : "text-slate-500"
          }`}
          title={`SSE: ${connection}`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              connection === "open"
                ? "bg-emerald-400"
                : connection === "error"
                  ? "bg-rose-400"
                  : "bg-slate-500"
            }`}
          />
          {connection === "open" ? "live" : connection}
        </span>
      </header>

      {streamErrors.length > 0 && (
        <div className="shrink-0 border-b border-amber-900/50 bg-amber-950/30 px-5 py-2 text-xs text-amber-200">
          {(streamErrors[streamErrors.length - 1] as { message: string }).message}
        </div>
      )}

      <main className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1.3fr_1fr_1fr]">
        <div className="min-h-0 overflow-y-auto border-slate-800 lg:border-r">
          <Transcript events={events} />
        </div>
        <div className="min-h-0 overflow-hidden border-slate-800 lg:border-r">
          <PlanPanel
            plan={plan}
            deadlock={deadlock}
            phase={phase}
            onApprove={approve}
            busy={busy}
          />
        </div>
        <div className="min-h-0 overflow-hidden">
          <WriteLog events={events} />
        </div>
      </main>
    </div>
  );
}

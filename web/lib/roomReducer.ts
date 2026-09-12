// The event reducer. One pure function: every SSE event maps to exactly one case.
// Everything else in the frontend is presentational.

import type {
  Analysis,
  FinalPlan,
  PoA,
  Provider,
  SSEEvent,
  Ticket,
  TicketStatus,
} from "./contract";

/* ---------------------------------- state ----------------------------------- */

export interface Participant {
  user_id: string;
  display_name: string;
  provider?: Provider;
  model?: string;
  /** This browser's user. */
  is_local?: boolean;
  /** A locally pinned identity wins over whatever the stream says. */
  pinned?: boolean;
}

export interface FileState {
  path: string;
  content: string;
  /** Content the file takes once its owning agent writes it. */
  pending_content?: string;
  language: string;
  last_written_by?: string;
  written_at?: string;
  /** Rejected write attempts against this path, newest last. */
  rejected_by: { agent_id: string; ticket_id: string; reason: string; ts: string }[];
}

export type Phase =
  | "lobby"
  | "drafting"
  | "negotiating"
  | "awaiting_approval"
  | "approved"
  | "executing"
  | "done";

/** One entry in the chat stream. Cards hold ids only and read live state. */
export type ChatItem =
  | { id: string; ts: string; kind: "participant_joined"; user_id: string }
  | { id: string; ts: string; kind: "poa"; agent_id: string; user_id: string }
  | { id: string; ts: string; kind: "analysis"; round: number }
  | {
      id: string;
      ts: string;
      kind: "agent_message";
      round: number;
      agent_id: string;
      content: string;
      addresses_issues: string[];
    }
  | {
      id: string;
      ts: string;
      kind: "user_message";
      round: number;
      user_id: string;
      content: string;
    }
  | { id: string; ts: string; kind: "moderator_message"; round: number; content: string }
  | { id: string; ts: string; kind: "round_complete"; round: number }
  | { id: string; ts: string; kind: "plan"; plan_id: string }
  | { id: string; ts: string; kind: "approval"; user_id: string; approved: boolean }
  | { id: string; ts: string; kind: "plan_approved"; plan_id: string }
  | { id: string; ts: string; kind: "context_updated"; version: number; content: string }
  | { id: string; ts: string; kind: "tickets" }
  | { id: string; ts: string; kind: "ticket_started"; ticket_id: string; agent_id: string }
  | { id: string; ts: string; kind: "ticket_completed"; ticket_id: string; status: TicketStatus }
  | {
      id: string;
      ts: string;
      kind: "file_written";
      ticket_id: string;
      agent_id: string;
      path: string;
      accepted: boolean;
      reason: string | null;
    }
  | { id: string; ts: string; kind: "error"; where: string; detail: string };

export interface RoomState {
  room_id: string | null;
  phase: Phase;
  round: number;
  participants: Record<string, Participant>; // user_id -> participant
  /** agent_id -> user_id, learned from poa_generated. */
  agent_owner: Record<string, string>;
  poas: Record<string, PoA>; // agent_id -> PoA
  analyses: Analysis[]; // one per round, in order
  items: ChatItem[];
  plan: FinalPlan | null;
  approvals: Record<string, boolean>; // user_id -> approved
  tickets: Ticket[];
  context: { version: number; content: string } | null;
  files: Record<string, FileState>; // path -> file
  errors: { where: string; detail: string; ts: string }[];
  /** Monotonic counter so every ChatItem gets a stable unique key. */
  seq: number;
}

export const initialRoomState: RoomState = {
  room_id: null,
  phase: "lobby",
  round: 0,
  participants: {},
  agent_owner: {},
  poas: {},
  analyses: [],
  items: [],
  plan: null,
  approvals: {},
  tickets: [],
  context: null,
  files: {},
  errors: [],
  seq: 0,
};

/* --------------------------------- helpers ---------------------------------- */

/** Omit that distributes over a union, so each ChatItem variant keeps its own fields. */
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

function withItem(state: RoomState, item: DistributiveOmit<ChatItem, "id">): RoomState {
  const id = `i${state.seq}`;
  return {
    ...state,
    seq: state.seq + 1,
    items: [...state.items, { ...item, id } as ChatItem],
  };
}

function patchTicket(tickets: Ticket[], ticket_id: string, patch: Partial<Ticket>): Ticket[] {
  return tickets.map((t) => (t.ticket_id === ticket_id ? { ...t, ...patch } : t));
}

export function currentAnalysis(state: RoomState): Analysis | null {
  return state.analyses.length ? state.analyses[state.analyses.length - 1] : null;
}

export function analysisForRound(state: RoomState, round: number): Analysis | null {
  return state.analyses.find((a) => a.round === round) ?? null;
}

export function blockingCount(a: Analysis): number {
  return a.differences.filter((d) => d.severity === "blocking").length;
}

export function ticketById(state: RoomState, ticket_id: string): Ticket | undefined {
  return state.tickets.find((t) => t.ticket_id === ticket_id);
}

export function userOfAgent(state: RoomState, agent_id: string): Participant | undefined {
  const user_id = state.agent_owner[agent_id];
  return user_id ? state.participants[user_id] : undefined;
}

export function providerOfAgent(state: RoomState, agent_id: string): Provider | undefined {
  return userOfAgent(state, agent_id)?.provider;
}

/* --------------------------------- reducer ---------------------------------- */

export function roomReducer(state: RoomState, event: SSEEvent): RoomState {
  const base: RoomState = { ...state, room_id: state.room_id ?? event.room_id };

  switch (event.type) {
    case "participant_joined": {
      const existing = base.participants[event.user_id];
      const participant: Participant = {
        user_id: event.user_id,
        display_name: event.display_name ?? existing?.display_name ?? event.user_id,
        // A locally pinned identity is not overwritten by the stream.
        provider: existing?.pinned ? existing.provider : event.provider,
        model: existing?.pinned ? existing.model : event.model,
        is_local: existing?.is_local,
        pinned: existing?.pinned,
      };
      const next = {
        ...base,
        participants: { ...base.participants, [event.user_id]: participant },
      };
      // A re-join (the local identity arriving again from the stream) is not a new row.
      if (existing) return next;
      return withItem(next, { ts: event.ts, kind: "participant_joined", user_id: event.user_id });
    }

    case "poa_generated": {
      const next: RoomState = {
        ...base,
        phase: base.phase === "lobby" ? "drafting" : base.phase,
        poas: { ...base.poas, [event.agent_id]: event.poa },
        agent_owner: { ...base.agent_owner, [event.agent_id]: event.user_id },
      };
      return withItem(next, {
        ts: event.ts,
        kind: "poa",
        agent_id: event.agent_id,
        user_id: event.user_id,
      });
    }

    case "analysis": {
      const analysis: Analysis = {
        round: event.round,
        similarities: event.similarities,
        differences: event.differences,
        converged: event.converged,
      };
      const analyses = [...base.analyses.filter((a) => a.round !== event.round), analysis].sort(
        (a, b) => a.round - b.round,
      );
      const next: RoomState = {
        ...base,
        phase: "negotiating",
        round: Math.max(base.round, event.round),
        analyses,
      };
      return withItem(next, { ts: event.ts, kind: "analysis", round: event.round });
    }

    case "agent_message":
      return withItem(
        { ...base, round: Math.max(base.round, event.round) },
        {
          ts: event.ts,
          kind: "agent_message",
          round: event.round,
          agent_id: event.agent_id,
          content: event.content,
          addresses_issues: event.addresses_issues,
        },
      );

    case "user_message":
      return withItem(
        { ...base, round: Math.max(base.round, event.round) },
        {
          ts: event.ts,
          kind: "user_message",
          round: event.round,
          user_id: event.user_id,
          content: event.content,
        },
      );

    case "moderator_message":
      return withItem(
        { ...base, round: Math.max(base.round, event.round) },
        { ts: event.ts, kind: "moderator_message", round: event.round, content: event.content },
      );

    case "round_complete":
      return withItem(base, { ts: event.ts, kind: "round_complete", round: event.round });

    case "plan_proposed": {
      const next: RoomState = {
        ...base,
        phase: "awaiting_approval",
        plan: event.plan,
        approvals: { ...event.plan.approvals },
      };
      return withItem(next, { ts: event.ts, kind: "plan", plan_id: event.plan.plan_id });
    }

    case "approval_updated": {
      // Idempotent: a duplicate approval (the local click, then the same event off the
      // stream) updates the tally without adding a second row.
      const known = base.approvals[event.user_id];
      const approvals = { ...base.approvals, [event.user_id]: event.approved };
      const next: RoomState = {
        ...base,
        approvals,
        plan: base.plan ? { ...base.plan, approvals } : null,
      };
      if (known === event.approved) return next;
      return withItem(next, {
        ts: event.ts,
        kind: "approval",
        user_id: event.user_id,
        approved: event.approved,
      });
    }

    case "plan_approved": {
      const next: RoomState = {
        ...base,
        phase: "approved",
        plan: base.plan ? { ...base.plan, status: "approved" } : null,
      };
      return withItem(next, { ts: event.ts, kind: "plan_approved", plan_id: event.plan_id });
    }

    case "context_updated": {
      const next: RoomState = {
        ...base,
        context: { version: event.version, content: event.content },
      };
      return withItem(next, {
        ts: event.ts,
        kind: "context_updated",
        version: event.version,
        content: event.content,
      });
    }

    case "tickets_created": {
      const next: RoomState = { ...base, tickets: event.tickets };
      return withItem(next, { ts: event.ts, kind: "tickets" });
    }

    case "ticket_started": {
      const next: RoomState = {
        ...base,
        phase: "executing",
        tickets: patchTicket(base.tickets, event.ticket_id, { status: "running" }),
      };
      return withItem(next, {
        ts: event.ts,
        kind: "ticket_started",
        ticket_id: event.ticket_id,
        agent_id: event.agent_id,
      });
    }

    case "ticket_completed": {
      const tickets = patchTicket(base.tickets, event.ticket_id, { status: event.status });
      const allSettled =
        tickets.length > 0 && tickets.every((t) => t.status === "done" || t.status === "failed");
      const next: RoomState = { ...base, tickets, phase: allSettled ? "done" : base.phase };
      return withItem(next, {
        ts: event.ts,
        kind: "ticket_completed",
        ticket_id: event.ticket_id,
        status: event.status,
      });
    }

    case "file_written": {
      const file = base.files[event.path];
      let files = base.files;
      if (file) {
        files = {
          ...base.files,
          [event.path]: event.accepted
            ? {
                ...file,
                content: file.pending_content ?? file.content,
                last_written_by: event.agent_id,
                written_at: event.ts,
              }
            : {
                ...file,
                rejected_by: [
                  ...file.rejected_by,
                  {
                    agent_id: event.agent_id,
                    ticket_id: event.ticket_id,
                    reason: event.reason ?? "rejected",
                    ts: event.ts,
                  },
                ],
              },
        };
      }
      return withItem(
        { ...base, files },
        {
          ts: event.ts,
          kind: "file_written",
          ticket_id: event.ticket_id,
          agent_id: event.agent_id,
          path: event.path,
          accepted: event.accepted,
          reason: event.reason,
        },
      );
    }

    case "error": {
      const next: RoomState = {
        ...base,
        errors: [...base.errors, { where: event.where, detail: event.detail, ts: event.ts }],
      };
      return withItem(next, {
        ts: event.ts,
        kind: "error",
        where: event.where,
        detail: event.detail,
      });
    }

    default: {
      // Unknown event types are ignored, never thrown on: model output is untrusted.
      const _exhaustive: never = event;
      void _exhaustive;
      return state;
    }
  }
}

/** Replay a whole stream from scratch. Used by the fixture player and on SSE reconnect. */
export function replay(events: SSEEvent[], from: RoomState = initialRoomState): RoomState {
  return events.reduce(roomReducer, from);
}

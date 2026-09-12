// The architecture of the frontend: one pure function, one case per event type.
// The switch is exhaustive — the `never` check at the bottom is a compile error
// the day the contract grows a seventeenth event.

import type {
  Analysis,
  Difference,
  FileWritten,
  FinalPlan,
  Participant,
  PoA,
  Provider,
  SSEEvent,
  Ticket,
  TicketStatus,
} from "./types";

/**
 * The chat is ONE ordered array of heterogeneous items. Agent messages, K2
 * messages, user messages, the analysis card and the plan approval card all live
 * here in arrival order — that is what makes the negotiation read as a single
 * conversation instead of four disconnected panels, and it makes a new card a
 * render case rather than a new layout.
 *
 * Items that never change carry their own payload (analysis, poa). Items whose
 * content keeps moving (the plan's approvals, ticket statuses) carry an id and
 * read live off RoomState.
 */
export type ChatItem =
  | { kind: "user"; id: string; ts: string; round: number; userId: string; content: string }
  | {
      kind: "agent";
      id: string;
      ts: string;
      round: number;
      agentId: string;
      content: string;
      addressesIssues: string[];
    }
  | { kind: "moderator"; id: string; ts: string; round: number; content: string }
  | { kind: "analysis"; id: string; ts: string; analysis: Analysis }
  | { kind: "plan"; id: string; ts: string; planId: string }
  | { kind: "poa"; id: string; ts: string; poa: PoA }
  | { kind: "tickets"; id: string; ts: string }
  | { kind: "participant"; id: string; ts: string; userId: string }
  | { kind: "roundComplete"; id: string; ts: string; round: number }
  | { kind: "approval"; id: string; ts: string; userId: string; approved: boolean }
  | { kind: "planApproved"; id: string; ts: string; planId: string }
  | { kind: "context"; id: string; ts: string; version: number; content: string }
  | { kind: "ticketStarted"; id: string; ts: string; ticketId: string; agentId: string }
  | { kind: "ticketCompleted"; id: string; ts: string; ticketId: string; status: TicketStatus }
  | { kind: "write"; id: string; ts: string; write: FileWritten }
  | { kind: "error"; id: string; ts: string; where: string; detail: string };

export type RoomStatus =
  | "setup"
  | "tasks"
  | "negotiating"
  | "awaiting_approval"
  | "executing"
  | "done";

export type RoomState = {
  roomId: string;
  participants: Participant[];
  messages: ChatItem[]; // user | agent | moderator | analysis card | plan card | ...
  currentRound: number;
  latestAnalysis: Analysis | null;
  proposedPlan: FinalPlan | null;
  approvals: Record<string, boolean>;
  tickets: Ticket[];
  files: Record<string, FileEntry>;
  writeLog: FileWritten[]; // both accepted and rejected
  sharedContext: { version: number; content: string } | null;
  status: RoomStatus;
};

export type FileEntry = {
  content: string;
  lastWrittenBy: string | null;
  /**
   * Phase 1 only: what this file becomes once its owning agent writes it. The
   * file_written event carries no body, so with a live orchestrator this is
   * replaced by a GET /rooms/{id}/files/{path} re-fetch on an accepted write.
   */
  pendingContent?: string;
};

export const initialRoomState: RoomState = {
  roomId: "",
  participants: [],
  messages: [],
  currentRound: 0,
  latestAnalysis: null,
  proposedPlan: null,
  approvals: {},
  tickets: [],
  files: {},
  writeLog: [],
  sharedContext: null,
  status: "setup",
};

/* --------------------------------- helpers ---------------------------------- */

type NewChatItem<T extends ChatItem = ChatItem> = T extends unknown ? Omit<T, "id"> : never;

/** Append a chat item with an id that is stable across a replay. */
function say(state: RoomState, item: NewChatItem): RoomState {
  return {
    ...state,
    messages: [...state.messages, { ...item, id: `m${state.messages.length}` } as ChatItem],
  };
}

function upsertParticipant(
  participants: Participant[],
  user_id: string,
  patch: Partial<Participant>,
): Participant[] {
  const i = participants.findIndex((p) => p.user_id === user_id);
  if (i === -1) {
    return [
      ...participants,
      { user_id, display_name: user_id, provider: "claude", model: "", ...patch },
    ];
  }
  const next = [...participants];
  next[i] = { ...next[i], ...patch };
  return next;
}

function patchTicket(tickets: Ticket[], ticket_id: string, patch: Partial<Ticket>): Ticket[] {
  return tickets.map((t) => (t.ticket_id === ticket_id ? { ...t, ...patch } : t));
}

/* -------------------------------- selectors --------------------------------- */

export function participantById(state: RoomState, user_id: string): Participant | undefined {
  return state.participants.find((p) => p.user_id === user_id);
}

/** The user an agent argues for. Learned from poa_generated. */
export function participantOfAgent(state: RoomState, agent_id: string): Participant | undefined {
  return state.participants.find((p) => p.agent_id === agent_id);
}

export function providerOfAgent(state: RoomState, agent_id: string): Provider | undefined {
  return participantOfAgent(state, agent_id)?.provider;
}

export function localParticipant(state: RoomState): Participant | undefined {
  return state.participants.find((p) => p.is_local);
}

/**
 * Find a difference by issue_id, newest analysis first. Agent messages cite
 * issue_ids; this is what turns those citations into topic and severity.
 */
export function differenceById(state: RoomState, issueId: string): Difference | undefined {
  for (let i = state.messages.length - 1; i >= 0; i--) {
    const m = state.messages[i];
    if (m.kind !== "analysis") continue;
    const found = m.analysis.differences.find((d) => d.issue_id === issueId);
    if (found) return found;
  }
  return undefined;
}

export function blockingCount(a: Analysis): number {
  return a.differences.filter((d) => d.severity === "blocking").length;
}

/** Refused writes against a path, for the tree marker and the viewer banner. */
export function rejectedWrites(state: RoomState, path: string): FileWritten[] {
  return state.writeLog.filter((w) => w.path === path && !w.accepted);
}

/* --------------------------------- reducer ---------------------------------- */

export function roomReducer(state: RoomState, event: SSEEvent): RoomState {
  const base: RoomState = { ...state, roomId: state.roomId || event.room_id };

  switch (event.type) {
    case "participant_joined": {
      const known = participantById(base, event.user_id);
      const participants = upsertParticipant(base.participants, event.user_id, {
        display_name: event.display_name ?? known?.display_name ?? event.user_id,
        // A local identity, already pinned by the setup modal, is not overwritten.
        provider: known?.is_local ? known.provider : event.provider,
        model: known?.is_local ? known.model : event.model,
      });
      const next: RoomState = {
        ...base,
        participants,
        status: base.status === "setup" && participants.length >= 2 ? "tasks" : base.status,
      };
      // A re-join is a merge, not a second row in the chat.
      if (known) return next;
      return say(next, { kind: "participant", ts: event.ts, userId: event.user_id });
    }

    case "poa_generated": {
      const next: RoomState = {
        ...base,
        status: "negotiating",
        // This is where agent_id -> user_id is learned.
        participants: upsertParticipant(base.participants, event.user_id, {
          agent_id: event.agent_id,
        }),
      };
      return say(next, { kind: "poa", ts: event.ts, poa: event.poa });
    }

    case "analysis": {
      const analysis: Analysis = {
        round: event.round,
        similarities: event.similarities,
        differences: event.differences,
        converged: event.converged,
      };
      const next: RoomState = {
        ...base,
        status: "negotiating",
        currentRound: Math.max(base.currentRound, event.round),
        latestAnalysis: analysis,
      };
      return say(next, { kind: "analysis", ts: event.ts, analysis });
    }

    case "agent_message":
      return say(
        { ...base, currentRound: Math.max(base.currentRound, event.round) },
        {
          kind: "agent",
          ts: event.ts,
          round: event.round,
          agentId: event.agent_id,
          content: event.content,
          addressesIssues: event.addresses_issues,
        },
      );

    case "user_message":
      return say(
        { ...base, currentRound: Math.max(base.currentRound, event.round) },
        {
          kind: "user",
          ts: event.ts,
          round: event.round,
          userId: event.user_id,
          content: event.content,
        },
      );

    case "moderator_message":
      return say(
        { ...base, currentRound: Math.max(base.currentRound, event.round) },
        { kind: "moderator", ts: event.ts, round: event.round, content: event.content },
      );

    case "round_complete":
      return say(base, { kind: "roundComplete", ts: event.ts, round: event.round });

    case "plan_proposed": {
      const next: RoomState = {
        ...base,
        status: "awaiting_approval",
        proposedPlan: event.plan,
        approvals: { ...event.plan.approvals },
      };
      return say(next, { kind: "plan", ts: event.ts, planId: event.plan.plan_id });
    }

    case "approval_updated": {
      // Idempotent: the local click and the same event off the stream are one vote.
      const known = base.approvals[event.user_id];
      const approvals = { ...base.approvals, [event.user_id]: event.approved };
      const next: RoomState = {
        ...base,
        approvals,
        proposedPlan: base.proposedPlan ? { ...base.proposedPlan, approvals } : null,
      };
      if (known === event.approved) return next;
      return say(next, {
        kind: "approval",
        ts: event.ts,
        userId: event.user_id,
        approved: event.approved,
      });
    }

    case "plan_approved": {
      const next: RoomState = {
        ...base,
        status: "executing",
        proposedPlan: base.proposedPlan ? { ...base.proposedPlan, status: "approved" } : null,
      };
      return say(next, { kind: "planApproved", ts: event.ts, planId: event.plan_id });
    }

    case "context_updated": {
      const next: RoomState = {
        ...base,
        sharedContext: { version: event.version, content: event.content },
      };
      return say(next, {
        kind: "context",
        ts: event.ts,
        version: event.version,
        content: event.content,
      });
    }

    case "tickets_created":
      return say({ ...base, tickets: event.tickets }, { kind: "tickets", ts: event.ts });

    case "ticket_started": {
      const next: RoomState = {
        ...base,
        status: "executing",
        tickets: patchTicket(base.tickets, event.ticket_id, { status: "running" }),
      };
      return say(next, {
        kind: "ticketStarted",
        ts: event.ts,
        ticketId: event.ticket_id,
        agentId: event.agent_id,
      });
    }

    case "ticket_completed": {
      const tickets = patchTicket(base.tickets, event.ticket_id, { status: event.status });
      const settled = tickets.every((t) => t.status === "done" || t.status === "failed");
      const next: RoomState = {
        ...base,
        tickets,
        status: tickets.length > 0 && settled ? "done" : base.status,
      };
      return say(next, {
        kind: "ticketCompleted",
        ts: event.ts,
        ticketId: event.ticket_id,
        status: event.status,
      });
    }

    case "file_written": {
      const file = base.files[event.path];
      // A refused write changes no file. It only ever appears in the log.
      const files =
        file && event.accepted
          ? {
              ...base.files,
              [event.path]: {
                ...file,
                content: file.pendingContent ?? file.content,
                lastWrittenBy: event.agent_id,
              },
            }
          : base.files;
      const next: RoomState = { ...base, files, writeLog: [...base.writeLog, event] };
      return say(next, { kind: "write", ts: event.ts, write: event });
    }

    case "error":
      return say(base, {
        kind: "error",
        ts: event.ts,
        where: event.where,
        detail: event.detail,
      });

    default: {
      // Exhaustive: adding an event type to the contract breaks this line.
      const unhandled: never = event;
      void unhandled;
      return state;
    }
  }
}

/** Replay a whole stream. Used by the fixture player, and on an SSE reconnect. */
export function replay(events: SSEEvent[], from: RoomState = initialRoomState): RoomState {
  return events.reduce(roomReducer, from);
}

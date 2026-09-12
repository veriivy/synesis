"use client";

import { useCallback, useRef, useState } from "react";
import type { SSEEvent, SSEEventType } from "./types";
import { initialRoomState, roomReducer, type RoomState } from "./roomReducer";
import { fixtureEvents } from "./fixtures";
import { workspaceFiles } from "./workspace";
import { useFixturePlayer, type FixturePlayer } from "./useFixturePlayer";
import type { LocalIdentity } from "@/components/SetupModal";

/** Phase 1: this browser is u1. The rest of the room arrives off the fixture stream. */
export const LOCAL_USER_ID = "u1";
export const PEER_NAME = "Sam";
export const ROOM_ID = "room_demo01";
const FIRST_FILE = "README.md";

/** The stream carries no display_name, so give the peer one for the demo. */
export const demoEvents: SSEEvent[] = fixtureEvents.map((e) =>
  e.type === "participant_joined" && e.user_id !== LOCAL_USER_ID
    ? { ...e, display_name: PEER_NAME }
    : e,
);

/** Per-type dwell time, so the stream reads like a conversation rather than a dump. */
const DEMO_DELAY_MS: Partial<Record<SSEEventType, number>> = {
  participant_joined: 450,
  poa_generated: 1100,
  analysis: 1500,
  moderator_message: 1200,
  agent_message: 1200,
  user_message: 900,
  round_complete: 600,
  plan_proposed: 1200,
  approval_updated: 600,
  context_updated: 900,
  tickets_created: 1300,
  ticket_started: 450,
  file_written: 800,
  ticket_completed: 500,
};

export const demoDelayFor = (event: SSEEvent) => DEMO_DELAY_MS[event.type];

/** The workspace as GET /rooms/{id}/files would hand it over. */
export function seedRoom(): RoomState {
  return {
    ...initialRoomState,
    files: Object.fromEntries(
      workspaceFiles.map((f) => [
        f.path,
        { content: f.content, lastWrittenBy: null, pendingContent: f.after },
      ]),
    ),
  };
}

/** Join as the local user. is_local also pins the identity against the stream. */
export function joinLocal(prev: RoomState, identity: LocalIdentity): RoomState {
  const next = roomReducer(prev, {
    type: "participant_joined",
    room_id: ROOM_ID,
    ts: new Date().toISOString(),
    user_id: LOCAL_USER_ID,
    provider: identity.provider,
    model: identity.model,
    display_name: identity.display_name,
  });
  return {
    ...next,
    participants: next.participants.map((p) =>
      p.user_id === LOCAL_USER_ID ? { ...p, is_local: true } : p,
    ),
  };
}

export interface Room {
  player: FixturePlayer;
  state: RoomState;
  identity: LocalIdentity | null;
  join: (identity: LocalIdentity) => void;
  selected: string | null;
  select: (path: string | null) => void;
  approve: (userId: string, approved: boolean) => void;
  send: (content: string) => void;
}

/**
 * Everything the room needs on top of the player: who this browser is, which
 * file the viewer shows, and the two local actions (approve, interject). Both
 * `/` and `/dev` are thin wrappers around this.
 */
export function useRoom(options?: {
  identity?: LocalIdentity;
  intervalMs?: number;
  delayFor?: (event: SSEEvent) => number | undefined;
  /** Stop at the approval gate. The plan does not proceed without a human. */
  gateOnApproval?: boolean;
  autoPlay?: boolean;
}): Room {
  const { gateOnApproval = true } = options ?? {};
  const [identity, setIdentity] = useState<LocalIdentity | null>(options?.identity ?? null);
  const [selected, setSelected] = useState<string | null>(FIRST_FILE);

  // seed() is called on reset and on every seek, so it must see the current
  // identity without re-creating the player.
  const identityRef = useRef<LocalIdentity | null>(options?.identity ?? null);
  /** The API key never leaves this ref: not in state, not in storage, not in a log. */
  const apiKey = useRef<string | undefined>(options?.identity?.api_key);

  const seed = useCallback(() => {
    const base = seedRoom();
    return identityRef.current ? joinLocal(base, identityRef.current) : base;
  }, []);

  const player = useFixturePlayer({
    events: demoEvents,
    seed,
    intervalMs: options?.intervalMs,
    delayFor: options?.delayFor,
    pauseAfter: gateOnApproval ? ["plan_proposed"] : [],
    autoPlay: options?.autoPlay,
    // The viewer follows the agents: accepted writes and refused ones alike.
    onEvent: (event) => {
      if (event.type === "file_written") setSelected(event.path);
    },
  });

  const join = useCallback(
    (next: LocalIdentity) => {
      identityRef.current = next;
      apiKey.current = next.api_key;
      setIdentity(next);
      player.reset();
      player.play();
    },
    [player],
  );

  const approve = useCallback(
    (userId: string, approved: boolean) => {
      player.dispatch({
        type: "approval_updated",
        room_id: ROOM_ID,
        ts: new Date().toISOString(),
        user_id: userId,
        approved,
      });
      // Approving releases the gate; requesting changes holds the room.
      if (approved) player.play();
    },
    [player],
  );

  const send = useCallback(
    (content: string) => {
      player.dispatch({
        type: "user_message",
        room_id: ROOM_ID,
        ts: new Date().toISOString(),
        round: Math.max(player.state.currentRound, 1),
        user_id: LOCAL_USER_ID,
        content,
      });
    },
    [player],
  );

  return {
    player,
    state: player.state,
    identity,
    join,
    selected,
    select: setSelected,
    approve,
    send,
  };
}

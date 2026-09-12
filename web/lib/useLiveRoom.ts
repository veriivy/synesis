"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SSEEvent, SSEEventType } from "./types";
import { initialRoomState, roomReducer, type RoomState } from "./roomReducer";
import type { FixturePlayer } from "./useFixturePlayer";
import { LOCAL_USER_ID, PEER_NAME, joinLocal, type Room } from "./useRoom";
import type { LocalIdentity } from "@/components/SetupModal";

const ORCHESTRATOR_URL =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

/**
 * The orchestrator (orchestrator/events.py:format_sse) sends a named
 * `event: <type>` line per frame, not the unnamed `message` events
 * EventSource's onmessage listens for — so every type needs its own
 * addEventListener. onmessage stays wired too (harmlessly redundant) in
 * case that ever changes.
 */
const ALL_EVENT_TYPES: SSEEventType[] = [
  "participant_joined",
  "poa_generated",
  "analysis",
  "agent_message",
  "user_message",
  "moderator_message",
  "round_complete",
  "plan_proposed",
  "approval_updated",
  "plan_approved",
  "context_updated",
  "tickets_created",
  "ticket_started",
  "file_written",
  "ticket_completed",
  "error",
];

/** Returns the parsed JSON body on success (participant_token lives
 * there for /participants), or null on failure — logged, not thrown, so
 * one failed call doesn't take the whole room down client-side. */
async function postJSON(path: string, body: unknown): Promise<Record<string, unknown> | null> {
  const res = await fetch(`${ORCHESTRATOR_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    console.error(`${path} -> ${res.status}: ${await res.text()}`);
    return null;
  }
  return (await res.json()) as Record<string, unknown>;
}

/** The demo peer's requirement. The orchestrator drafts both PoAs with a
 * real model (agents.draft_poa), so there's no guaranteed mechanical way
 * to force a conflict the way a template stand-in could — this is worded
 * to plausibly collide with a browser-cookie-flavored requirement typed
 * into TaskIntake, mirroring how fixtures/PoA1.json and PoA2.json were
 * hand-written to conflict on purpose. */
export const DEMO_TASK_U2 =
  "Handle authentication using session cookies for the browser too.";

/**
 * The live counterpart to useRoom (see that file): identical `Room` shape,
 * backed by a real EventSource against the FastAPI orchestrator instead of
 * a timer over fixtures/stream.jsonl. roomReducer.ts and every component
 * under it are unchanged — this is exactly the swap useFixturePlayer.ts's
 * own docstring describes ("dropping the timer... the reducer and every
 * component stay as they are").
 *
 * Two things a live room needs that a fixture replay doesn't:
 *  - the workspace has to be fetched from the backend (GET /files, then
 *    GET /files/{path} per leaf) instead of read from workspace.ts, and an
 *    accepted file_written re-fetches that one file's content — the exact
 *    seam roomReducer.ts's FileEntry.pendingContent comment describes.
 *  - execution has no UI trigger anywhere in the current components, so
 *    this hook fires POST /execute itself the moment tickets_created
 *    arrives. The backend's own execute_started guard makes that safe to
 *    call more than once (a reconnect replaying its backlog, say) — a
 *    second call just 409s and is ignored here.
 */
/** useLiveRoom's return, extending Room with the local user's
 * participant_token — needed by callers outside this hook (app/live's
 * TaskIntake submit) that POST /tasks for LOCAL_USER_ID directly instead
 * of through a method this hook exposes. */
export interface LiveRoom extends Room {
  participantToken: string | null;
}

export function useLiveRoom(roomId: string): LiveRoom {
  const [state, setState] = useState<RoomState>(() => ({ ...initialRoomState, roomId }));
  const [identity, setIdentity] = useState<LocalIdentity | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [eventCount, setEventCount] = useState(0);
  const [participantToken, setParticipantToken] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const identityRef = useRef<LocalIdentity | null>(null);
  // The security fix (CLAUDE.md: no auth system, but a stolen BYO key is a
  // real hijack): the orchestrator now requires the token issued at first
  // join on every subsequent /tasks, /messages, /plan/approve call for
  // that user_id. Held in a ref (not just state) so approve()/send() —
  // fired from user clicks, not effects — always read the current value.
  const myTokenRef = useRef<string | null>(null);
  const peerTokenRef = useRef<string | null>(null);
  const executeTriggeredRef = useRef(false);
  const peerApprovedRef = useRef(false);

  const refetchFile = useCallback(
    async (path: string) => {
      const res = await fetch(`${ORCHESTRATOR_URL}/rooms/${roomId}/files/${path}`);
      if (!res.ok) return;
      const body = (await res.json()) as { content: string; last_written_by: string | null };
      setState((prev) => ({
        ...prev,
        files: {
          ...prev.files,
          [path]: { content: body.content, lastWrittenBy: body.last_written_by },
        },
      }));
    },
    [roomId],
  );

  const seedFiles = useCallback(async () => {
    const res = await fetch(`${ORCHESTRATOR_URL}/rooms/${roomId}/files`);
    if (!res.ok) return;
    const { tree } = (await res.json()) as { tree: unknown };
    const paths: string[] = [];
    const walk = (nodes: unknown) => {
      for (const n of nodes as { type: string; path: string; children?: unknown }[]) {
        if (n.type === "file") paths.push(n.path);
        else if (n.children) walk(n.children);
      }
    };
    walk(tree);
    await Promise.all(paths.map(refetchFile));
  }, [roomId, refetchFile]);

  // Imperative only — no setState in its own synchronous body, so it's
  // safe to call directly from the mount effect below. All the state
  // updates it wires up happen inside source.onmessage, a callback fired
  // later by the browser's SSE machinery, not synchronously during setup.
  const openSource = useCallback(() => {
    sourceRef.current?.close();
    executeTriggeredRef.current = false;
    peerApprovedRef.current = false;

    const source = new EventSource(`${ORCHESTRATOR_URL}/rooms/${roomId}/stream`);
    sourceRef.current = source;

    const handle = (msg: MessageEvent<string>) => {
      let event = JSON.parse(msg.data) as SSEEvent;
      // The backend's participant_joined never carries a display_name —
      // it's not in the frozen contract (web/lib/types.ts's comment on
      // ParticipantJoined.display_name says as much). The local user's
      // name comes from joinLocal's direct patch instead; the demo peer
      // (always "u2") needs the same treatment here that useRoom.ts's
      // demoEvents mapping already applies for the fixture path, or it
      // shows up as the bare string "u2" in the chat log and top bar.
      if (event.type === "participant_joined" && event.user_id === "u2" && !event.display_name) {
        event = { ...event, display_name: PEER_NAME };
      }
      setState((prev) => roomReducer(prev, event));
      setEventCount((n) => n + 1);

      if (event.type === "file_written") {
        setSelected(event.path);
        if (event.accepted) void refetchFile(event.path);
      }
      if (event.type === "plan_proposed" && !peerApprovedRef.current && peerTokenRef.current) {
        peerApprovedRef.current = true;
        void postJSON(`/rooms/${roomId}/plan/approve`, {
          user_id: "u2",
          approved: true,
          participant_token: peerTokenRef.current,
        });
      }
      if (event.type === "tickets_created" && !executeTriggeredRef.current) {
        executeTriggeredRef.current = true;
        void postJSON(`/rooms/${roomId}/execute`, {});
      }
    };

    source.onmessage = handle;
    for (const type of ALL_EVENT_TYPES) {
      source.addEventListener(type, handle);
    }
  }, [roomId, refetchFile]);

  useEffect(() => {
    openSource();
    void seedFiles();
    return () => sourceRef.current?.close();
  }, [openSource, seedFiles]);

  // Explicit reconnect (the transport "reset" control only — never called
  // from the mount effect above): drops local state and reopens, which
  // replays the server's backlog from the top.
  const reconnect = useCallback(() => {
    setEventCount(0);
    setState(() => {
      const base = { ...initialRoomState, roomId };
      return identityRef.current ? joinLocal(base, identityRef.current) : base;
    });
    openSource();
    void seedFiles();
  }, [roomId, openSource, seedFiles]);

  const join = useCallback(
    (next: LocalIdentity) => {
      identityRef.current = next;
      // The backend's /negotiate rejects a live-tasks room with fewer than
      // 2 participants (orchestrator/main.py). Both joins — and the demo
      // peer's task — have to land before `identity` flips and TaskIntake
      // appears, or a fast submit can race ahead of u2's setup and 409.
      void (async () => {
        const mine = await postJSON(`/rooms/${roomId}/participants`, {
          user_id: LOCAL_USER_ID,
          display_name: next.display_name,
          provider: next.provider,
          model: next.model,
          api_key: next.api_key,
        });
        const myToken = (mine?.participant_token as string | undefined) ?? null;
        myTokenRef.current = myToken;

        // Phase 1: this browser is the only real client, so the peer is a
        // fixed second participant (mirrors useRoom.ts's PEER_NAME) rather
        // than a second real browser — true multi-browser rooms are
        // future work, not part of wiring the stream itself.
        const peer = await postJSON(`/rooms/${roomId}/participants`, {
          user_id: "u2",
          display_name: PEER_NAME,
          provider: "gpt",
          model: "gpt-5",
        });
        const peerToken = (peer?.participant_token as string | undefined) ?? null;
        peerTokenRef.current = peerToken;
        await postJSON(`/rooms/${roomId}/tasks`, {
          user_id: "u2",
          tasks: [{ text: DEMO_TASK_U2, priority: "must" }],
          participant_token: peerToken,
        });

        setParticipantToken(myToken);
        setIdentity(next);
        setState((prev) => joinLocal(prev, next));
      })();
    },
    [roomId],
  );

  const approve = useCallback(
    (userId: string, approved: boolean) => {
      void postJSON(`/rooms/${roomId}/plan/approve`, {
        user_id: userId,
        approved,
        participant_token:
          userId === LOCAL_USER_ID ? myTokenRef.current : peerTokenRef.current,
      });
    },
    [roomId],
  );

  const send = useCallback(
    (content: string) => {
      void postJSON(`/rooms/${roomId}/messages`, {
        user_id: LOCAL_USER_ID,
        content,
        participant_token: myTokenRef.current,
      });
    },
    [roomId],
  );

  // Scrubbing/speed controls don't apply to a live feed — TopBar still
  // renders them (it's shared with the fixture player), so they're wired
  // as inert here rather than touching TopBar to hide them. "reset" is the
  // one that keeps real meaning: it fully reconnects, which replays the
  // server's backlog from the top.
  const player: FixturePlayer = {
    state,
    events: [],
    index: eventCount,
    total: eventCount,
    playing: true,
    speed: 1,
    atEnd: true,
    lastEvent: null,
    nextEvent: null,
    play: () => {},
    pause: () => {},
    toggle: () => {},
    step: () => {},
    reset: reconnect,
    seekTo: () => {},
    setSpeed: () => {},
    dispatch: (event) => setState((prev) => roomReducer(prev, event)),
  };

  return {
    player,
    state,
    identity,
    join,
    selected,
    select: setSelected,
    approve,
    send,
    participantToken,
  };
}

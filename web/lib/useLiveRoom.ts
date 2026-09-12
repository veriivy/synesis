"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SSEEvent } from "./types";
import { initialRoomState, roomReducer, type RoomState } from "./roomReducer";
import type { FixturePlayer } from "./useFixturePlayer";
import { LOCAL_USER_ID, PEER_NAME, joinLocal, type Room } from "./useRoom";
import type { LocalIdentity } from "@/components/SetupModal";

const ORCHESTRATOR_URL =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

async function postJSON(path: string, body: unknown): Promise<void> {
  const res = await fetch(`${ORCHESTRATOR_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    console.error(`${path} -> ${res.status}: ${await res.text()}`);
  }
}

/** The demo peer's requirement, deliberately worded to collide with
 * whatever file the local user's own task text slugifies to would need
 * hand-authoring to guarantee — so instead this seeds a known-colliding
 * pair by default; see DEMO_TASK_U2 usage in app/live/page.tsx. */
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
export function useLiveRoom(roomId: string): Room {
  const [state, setState] = useState<RoomState>(() => ({ ...initialRoomState, roomId }));
  const [identity, setIdentity] = useState<LocalIdentity | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [eventCount, setEventCount] = useState(0);
  const sourceRef = useRef<EventSource | null>(null);
  const identityRef = useRef<LocalIdentity | null>(null);
  const executeTriggeredRef = useRef(false);

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

    const source = new EventSource(`${ORCHESTRATOR_URL}/rooms/${roomId}/stream`);
    sourceRef.current = source;
    source.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as SSEEvent;
      setState((prev) => roomReducer(prev, event));
      setEventCount((n) => n + 1);

      if (event.type === "file_written") {
        setSelected(event.path);
        if (event.accepted) void refetchFile(event.path);
      }
      if (event.type === "tickets_created" && !executeTriggeredRef.current) {
        executeTriggeredRef.current = true;
        void postJSON(`/rooms/${roomId}/execute`, {});
      }
    };
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
      setIdentity(next);
      setState((prev) => joinLocal(prev, next));
      void postJSON(`/rooms/${roomId}/participants`, {
        user_id: LOCAL_USER_ID,
        display_name: next.display_name,
        provider: next.provider,
        model: next.model,
        api_key: next.api_key,
      });
      // Phase 1: this browser is the only real client, so the peer is a
      // fixed second participant (mirrors useRoom.ts's PEER_NAME) rather
      // than a second real browser — true multi-browser rooms are future
      // work, not part of wiring the stream itself.
      void postJSON(`/rooms/${roomId}/participants`, {
        user_id: "u2",
        display_name: PEER_NAME,
        provider: "gpt",
        model: "gpt-5",
      }).then(() =>
        postJSON(`/rooms/${roomId}/tasks`, {
          user_id: "u2",
          tasks: [{ text: DEMO_TASK_U2, priority: "must" }],
        }),
      );
    },
    [roomId],
  );

  const approve = useCallback(
    (userId: string, approved: boolean) => {
      void postJSON(`/rooms/${roomId}/plan/approve`, { user_id: userId, approved });
    },
    [roomId],
  );

  const send = useCallback(
    (content: string) => {
      void postJSON(`/rooms/${roomId}/messages`, { user_id: LOCAL_USER_ID, content });
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
  };
}

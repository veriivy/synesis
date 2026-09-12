"use client";

/**
 * Subscribes to a room's SSE stream and accumulates events.
 *
 * Two things matter here beyond "it works":
 *   - The server replays history on connect, so a reconnect must not duplicate what is
 *     already on screen. Events are keyed and deduped.
 *   - EventSource reconnects on its own, but a dead backend should be visible in the
 *     UI rather than looking like agents that went quiet.
 *
 * EventSource is browser-only, so this module is a client module. It runs inside
 * useEffect, which never executes during server rendering — but the directive keeps
 * that guarantee local to this file instead of depending on every caller.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { streamUrl } from "./api";
import type { SynesisEvent, Workplan } from "./types";

export type Connection = "idle" | "connecting" | "open" | "error";

const KNOWN_TYPES: SynesisEvent["type"][] = [
  "agent_message",
  "round_complete",
  "plan_proposed",
  "deadlock",
  "file_written",
  "commit",
  "intent_registered",
  "error",
  "plan_rejected",
];

/** Identity for dedupe. Two events with the same key are the same event replayed. */
function eventKey(event: SynesisEvent): string {
  switch (event.type) {
    case "agent_message":
      return `msg:${event.round}:${event.agent_id}`;
    case "round_complete":
      return `round:${event.round}`;
    case "plan_proposed":
      return `plan:${event.plan.plan_id}`;
    case "commit":
      return `commit:${event.sha}`;
    case "file_written":
      return `write:${event.agent_id}:${event.path}:${event.ts}`;
    default:
      return `${event.type}:${event.ts}`;
  }
}

export function useRoomStream(roomId: string | null) {
  const [events, setEvents] = useState<SynesisEvent[]>([]);
  const [connection, setConnection] = useState<Connection>("idle");
  const seen = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!roomId) return;

    seen.current = new Set();
    setEvents([]);
    setConnection("connecting");

    const source = new EventSource(streamUrl(roomId));

    const onEvent = (raw: MessageEvent) => {
      let parsed: SynesisEvent;
      try {
        parsed = JSON.parse(raw.data) as SynesisEvent;
      } catch {
        return; // a malformed frame must not take the transcript down
      }
      const key = eventKey(parsed);
      if (seen.current.has(key)) return;
      seen.current.add(key);
      setEvents((prior) => [...prior, parsed]);
    };

    source.onopen = () => setConnection("open");
    source.onerror = () => setConnection("error");
    // The server names each frame with `event:`, so the default `message` handler
    // never fires — every type has to be subscribed explicitly.
    KNOWN_TYPES.forEach((type) => source.addEventListener(type, onEvent as EventListener));
    source.onmessage = onEvent;

    return () => source.close();
  }, [roomId]);

  const plan = useMemo<Workplan | null>(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const event = events[i];
      if (event.type === "plan_proposed") return event.plan;
    }
    return null;
  }, [events]);

  return { events, connection, plan };
}

"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { SSEEvent, SSEEventType } from "@/lib/contract";
import { initialRoomState, roomReducer, type RoomState } from "@/lib/roomReducer";
import { fixtureStream } from "@/lib/fixtures/stream";
import { workspaceFiles } from "@/lib/fixtures/workspace";
import { ChatPanel } from "@/components/ChatPanel";
import { CodePanel } from "@/components/CodePanel";
import { SetupModal, type LocalIdentity } from "@/components/SetupModal";
import { TopBar } from "@/components/TopBar";

/** Phase 1: this browser is u1. The rest of the room arrives off the fixture stream. */
const LOCAL_USER_ID = "u1";
const PEER_NAME = "Sam";
const ROOM_ID = "room_demo01";
const FIRST_FILE = "README.md";

/** Per-type dwell time, so the stream reads like a conversation rather than a dump. */
const DELAY_MS: Partial<Record<SSEEventType, number>> & { default: number } = {
  default: 700,
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

function seedState(): RoomState {
  return {
    ...initialRoomState,
    files: Object.fromEntries(
      workspaceFiles.map((f) => [
        f.path,
        {
          path: f.path,
          content: f.content,
          pending_content: f.after,
          language: f.language,
          rejected_by: [],
        },
      ]),
    ),
  };
}

/** Join as the local user: the identity is pinned so the stream cannot overwrite it. */
function joinLocal(prev: RoomState, identity: LocalIdentity): RoomState {
  const next = roomReducer(prev, {
    type: "participant_joined",
    room_id: ROOM_ID,
    ts: new Date().toISOString(),
    user_id: LOCAL_USER_ID,
    provider: identity.provider,
    model: identity.model,
    display_name: identity.display_name,
  });
  const p = next.participants[LOCAL_USER_ID];
  return {
    ...next,
    participants: { ...next.participants, [LOCAL_USER_ID]: { ...p, is_local: true, pinned: true } },
  };
}

export default function Room() {
  const [state, setState] = useState<RoomState>(seedState);
  const [identity, setIdentity] = useState<LocalIdentity | null>(null);
  const [selected, setSelected] = useState<string | null>(FIRST_FILE);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  /** The API key never leaves this ref: not in state, not in storage, not in a log. */
  const apiKey = useRef<string | undefined>(undefined);

  // The peer has no display_name on the wire, so give them one for the demo.
  const events: SSEEvent[] = useMemo(
    () =>
      fixtureStream.map((e) =>
        e.type === "participant_joined" && e.user_id !== LOCAL_USER_ID
          ? { ...e, display_name: PEER_NAME }
          : e,
      ),
    [],
  );

  function apply(event: SSEEvent) {
    setState((prev) => roomReducer(prev, event));
    // The viewer follows the agents: both accepted writes and refused ones.
    if (event.type === "file_written") setSelected(event.path);
  }

  function advance() {
    const event = events[index];
    if (!event) return;
    apply(event);
    setIndex((i) => i + 1);
    // Stop at the approval gate. The plan does not proceed without a human.
    if (event.type === "plan_proposed") setPlaying(false);
  }

  const atEnd = index >= events.length;

  useEffect(() => {
    const event = events[index];
    if (!playing || !event) return;
    const delay = (DELAY_MS[event.type] ?? DELAY_MS.default) / speed;
    const timer = setTimeout(advance, delay);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, index, speed, events]);

  function join(next: LocalIdentity) {
    apiKey.current = next.api_key;
    setIdentity(next);
    setState((prev) => joinLocal(prev, next));
    setPlaying(true);
  }

  function restart() {
    setPlaying(false);
    setIndex(0);
    setSelected(FIRST_FILE);
    setState(identity ? joinLocal(seedState(), identity) : seedState());
  }

  function approve(user_id: string, approved: boolean) {
    apply({
      type: "approval_updated",
      room_id: ROOM_ID,
      ts: new Date().toISOString(),
      user_id,
      approved,
    });
    // Approving releases the gate; requesting changes holds the room.
    if (approved) setPlaying(true);
  }

  function send(content: string) {
    apply({
      type: "user_message",
      room_id: ROOM_ID,
      ts: new Date().toISOString(),
      round: Math.max(state.round, 1),
      user_id: LOCAL_USER_ID,
      content,
    });
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar
        state={state}
        playback={{
          playing: playing && !atEnd,
          index,
          total: events.length,
          speed,
          toggle: () => setPlaying((p) => !p),
          step: () => {
            setPlaying(false);
            advance();
          },
          restart,
          setSpeed,
        }}
      />

      <main className="flex min-h-0 flex-1">
        <div className="w-[40%] min-w-[380px] shrink-0 border-r border-ide-border">
          <CodePanel state={state} selected={selected} onSelect={setSelected} />
        </div>
        <div className="min-w-0 flex-1">
          <ChatPanel
            state={state}
            onApprove={approve}
            onSelectFile={setSelected}
            onSend={send}
          />
        </div>
      </main>

      {!identity && <SetupModal onJoin={join} />}
    </div>
  );
}

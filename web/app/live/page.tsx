"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { RoomLayout } from "@/components/RoomLayout";
import { SetupModal } from "@/components/SetupModal";
import { TaskIntake } from "@/components/live/TaskIntake";
import { useLiveRoom } from "@/lib/useLiveRoom";
import type { Priority } from "@/lib/types";

const ORCHESTRATOR_URL =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

/**
 * The live counterpart to `/` (which replays fixtures/stream.jsonl): same
 * RoomLayout, driven by a real FastAPI orchestrator over SSE via
 * useLiveRoom instead of the fixture timer. Requires the orchestrator
 * running locally (see orchestrator/README.md) and reachable at
 * NEXT_PUBLIC_ORCHESTRATOR_URL (default http://localhost:8000).
 *
 * Pass ?room=<id> to reconnect to an existing room; otherwise this page
 * creates one on mount.
 */
function LiveRoomPage() {
  const params = useSearchParams();
  const roomIdParam = params.get("room");
  const [roomId, setRoomId] = useState<string | null>(roomIdParam);
  const [tasksSubmitted, setTasksSubmitted] = useState(Boolean(roomIdParam));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (roomId) return;
    fetch(`${ORCHESTRATOR_URL}/rooms`, { method: "POST" })
      .then((r) => {
        if (!r.ok) throw new Error(`POST /rooms -> ${r.status}`);
        return r.json();
      })
      .then((body: { room_id: string }) => setRoomId(body.room_id))
      .catch((e) => setError(String(e)));
  }, [roomId]);

  if (error) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-[12px] text-red-400">
        Couldn&apos;t reach the orchestrator at {ORCHESTRATOR_URL}: {error}
      </div>
    );
  }

  if (!roomId) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-[12px] text-ide-faint">
        creating room…
      </div>
    );
  }

  return (
    <LiveRoomBody
      roomId={roomId}
      tasksSubmitted={tasksSubmitted}
      onTasksSubmitted={() => setTasksSubmitted(true)}
    />
  );
}

function LiveRoomBody({
  roomId,
  tasksSubmitted,
  onTasksSubmitted,
}: {
  roomId: string;
  tasksSubmitted: boolean;
  onTasksSubmitted: () => void;
}) {
  const room = useLiveRoom(roomId);

  async function submitTasksAndNegotiate(tasks: { text: string; priority: Priority }[]) {
    if (!room.identity) return;
    await fetch(`${ORCHESTRATOR_URL}/rooms/${roomId}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // participant_token: required now that the orchestrator checks
      // ownership of a user_id (see orchestrator/main.py's _check_owner) —
      // without it this 403s instead of registering u1's tasks.
      body: JSON.stringify({ user_id: "u1", tasks, participant_token: room.participantToken }),
    });
    await fetch(`${ORCHESTRATOR_URL}/rooms/${roomId}/negotiate`, { method: "POST" });
    onTasksSubmitted();
  }

  return (
    <>
      <RoomLayout room={room} />
      {!room.identity && <SetupModal onJoin={room.join} />}
      {room.identity && !tasksSubmitted && <TaskIntake onSubmit={submitTasksAndNegotiate} />}
    </>
  );
}

export default function Page() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center font-mono text-[12px] text-ide-faint">
          loading…
        </div>
      }
    >
      <LiveRoomPage />
    </Suspense>
  );
}

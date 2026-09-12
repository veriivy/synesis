"use client";

import { RoomLayout } from "@/components/RoomLayout";
import { SetupModal } from "@/components/SetupModal";
import { demoDelayFor, useRoom } from "@/lib/useRoom";

export default function RoomPage() {
  // The demo rhythm: per-event dwell times rather than a flat tick.
  const room = useRoom({ delayFor: demoDelayFor });

  return (
    <>
      <RoomLayout room={room} />
      {!room.identity && <SetupModal onJoin={room.join} />}
    </>
  );
}

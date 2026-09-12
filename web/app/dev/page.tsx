"use client";

import { EventInspector } from "@/components/dev/EventInspector";
import { RoomLayout } from "@/components/RoomLayout";
import { DEFAULT_MODEL } from "@/components/SetupModal";
import { useRoom } from "@/lib/useRoom";

/**
 * The fixture replay harness, and the backup demo. No setup modal, no network:
 * it joins as a fixed identity and starts dispatching the fixture array through
 * roomReducer on the 1.2s tick the moment the page loads.
 */
export default function DevPage() {
  const room = useRoom({
    identity: { display_name: "Dev", provider: "claude", model: DEFAULT_MODEL.claude },
    autoPlay: true,
    // The harness runs the whole stream without waiting for a human.
    gateOnApproval: false,
  });

  return <RoomLayout room={room} rail={<EventInspector player={room.player} />} />;
}

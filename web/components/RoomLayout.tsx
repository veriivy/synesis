"use client";

import { type ReactNode, useState } from "react";
import type { Room } from "@/lib/useRoom";
import { ChatPanel } from "@/components/ChatPanel";
import { CodePanel } from "@/components/CodePanel";
import { Splitter } from "@/components/Splitter";
import { TopBar } from "@/components/TopBar";

const viewport = () => (typeof window === "undefined" ? 1600 : window.innerWidth);

/** Code on the left, the negotiation on the right, an optional rail after that. */
export function RoomLayout({ room, rail }: { room: Room; rail?: ReactNode }) {
  const [codeWidth, setCodeWidth] = useState(640);
  const [railWidth, setRailWidth] = useState(360);

  return (
    <div className="flex h-full flex-col">
      <TopBar state={room.state} player={room.player} />

      <main className="flex min-h-0 flex-1">
        <div className="shrink-0" style={{ width: codeWidth }}>
          <CodePanel state={room.state} selected={room.selected} onSelect={room.select} />
        </div>
        <Splitter
          axis="x"
          label="Code panel width"
          value={codeWidth}
          onChange={setCodeWidth}
          min={320}
          max={() => viewport() - 420}
        />

        <div className="min-w-0 flex-1">
          <ChatPanel
            state={room.state}
            onApprove={room.approve}
            onSelectFile={room.select}
            onSend={room.send}
          />
        </div>

        {rail && (
          <>
            <Splitter
              axis="x"
              label="Inspector width"
              value={railWidth}
              onChange={setRailWidth}
              min={260}
              max={() => viewport() - 600}
              inverted
            />
            <div className="shrink-0" style={{ width: railWidth }}>
              {rail}
            </div>
          </>
        )}
      </main>
    </div>
  );
}

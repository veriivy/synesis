"use client";

import type { RoomState } from "@/lib/roomReducer";
import { CodeViewer } from "@/components/CodeViewer";
import { FileTree } from "@/components/FileTree";

export function CodePanel({
  state,
  selected,
  onSelect,
}: {
  state: RoomState;
  selected: string | null;
  onSelect: (path: string) => void;
}) {
  return (
    <div className="flex h-full min-w-0">
      <div className="w-[210px] shrink-0">
        <FileTree state={state} selected={selected} onSelect={onSelect} />
      </div>
      <div className="min-w-0 flex-1">
        <CodeViewer state={state} path={selected} />
      </div>
    </div>
  );
}

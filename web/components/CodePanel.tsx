"use client";

import { useState } from "react";
import type { RoomState } from "@/lib/roomReducer";
import { CodeViewer } from "@/components/CodeViewer";
import { FileTree } from "@/components/FileTree";
import { Splitter } from "@/components/Splitter";

export function CodePanel({
  state,
  selected,
  onSelect,
}: {
  state: RoomState;
  selected: string | null;
  onSelect: (path: string) => void;
}) {
  const [treeWidth, setTreeWidth] = useState(210);

  return (
    <div className="flex h-full min-w-0">
      <div className="shrink-0" style={{ width: treeWidth }}>
        <FileTree state={state} selected={selected} onSelect={onSelect} />
      </div>
      <Splitter
        axis="x"
        label="File tree width"
        value={treeWidth}
        onChange={setTreeWidth}
        min={120}
        max={() => 420}
      />
      <div className="min-w-0 flex-1">
        <CodeViewer state={state} path={selected} />
      </div>
    </div>
  );
}

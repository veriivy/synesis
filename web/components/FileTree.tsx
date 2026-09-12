"use client";

import { useState } from "react";
import { type RoomState, participantOfAgent, rejectedWrites } from "@/lib/roomReducer";
import { buildTree, type TreeNode } from "@/lib/workspace";
import { PROVIDER_COLOR } from "@/components/ui";

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      className="h-3 w-3 shrink-0 text-ide-faint"
      style={{ transform: open ? "rotate(90deg)" : "none" }}
    >
      <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function Row({
  node,
  depth,
  state,
  selected,
  onSelect,
  open,
  toggle,
}: {
  node: TreeNode;
  depth: number;
  state: RoomState;
  selected: string | null;
  onSelect: (path: string) => void;
  open: Record<string, boolean>;
  toggle: (path: string) => void;
}) {
  const isDir = node.type === "dir";
  const file = isDir ? undefined : state.files[node.path];
  const writer = file?.lastWrittenBy ? participantOfAgent(state, file.lastWrittenBy) : undefined;
  const writerColor = writer?.provider ? PROVIDER_COLOR[writer.provider] : undefined;
  const refused = isDir ? [] : rejectedWrites(state, node.path);
  const isSelected = !isDir && selected === node.path;

  return (
    <>
      <button
        type="button"
        onClick={() => (isDir ? toggle(node.path) : onSelect(node.path))}
        className={`flex w-full items-center gap-1 py-[3px] pr-2 text-left text-[12px] ${
          isSelected ? "bg-ide-active text-ide-text" : "text-ide-dim hover:bg-ide-hover"
        }`}
        style={{ paddingLeft: 6 + depth * 12 }}
        title={file?.lastWrittenBy ? `last written by ${file.lastWrittenBy}` : node.path}
      >
        {isDir ? <Chevron open={!!open[node.path]} /> : <span className="w-3 shrink-0" />}
        <span className="truncate">{node.name}</span>
        {refused.length > 0 && (
          <span
            className="ml-auto font-mono text-[10px]"
            style={{ color: "var(--color-ide-blocking)" }}
            title={refused.at(-1)?.reason ?? undefined}
          >
            ✕{refused.length > 1 ? refused.length : ""}
          </span>
        )}
        {writerColor && (
          <span
            className={`${refused.length > 0 ? "ml-1" : "ml-auto"} h-1.5 w-1.5 shrink-0 rounded-full`}
            style={{ background: writerColor }}
          />
        )}
      </button>
      {isDir &&
        open[node.path] &&
        node.children.map((child) => (
          <Row
            key={child.path}
            node={child}
            depth={depth + 1}
            state={state}
            selected={selected}
            onSelect={onSelect}
            open={open}
            toggle={toggle}
          />
        ))}
    </>
  );
}

export function FileTree({
  state,
  selected,
  onSelect,
}: {
  state: RoomState;
  selected: string | null;
  onSelect: (path: string) => void;
}) {
  const tree = buildTree(Object.keys(state.files).sort());
  const [closed, setClosed] = useState<Record<string, boolean>>({});

  // Directories default to open; `closed` records the ones the reader collapsed.
  const open: Record<string, boolean> = {};
  const walk = (nodes: TreeNode[]) =>
    nodes.forEach((n) => {
      if (n.type === "dir") {
        open[n.path] = !closed[n.path];
        walk(n.children);
      }
    });
  walk(tree);

  return (
    <nav className="flex h-full flex-col border-r border-ide-border bg-ide-panel">
      <header className="flex h-8 shrink-0 items-center border-b border-ide-border px-3">
        <span className="font-mono text-[10px] tracking-widest text-ide-faint uppercase">
          workspace
        </span>
      </header>
      <div className="min-h-0 flex-1 overflow-auto py-1">
        {tree.map((node) => (
          <Row
            key={node.path}
            node={node}
            depth={0}
            state={state}
            selected={selected}
            onSelect={onSelect}
            open={open}
            toggle={(path) => setClosed((c) => ({ ...c, [path]: !c[path] }))}
          />
        ))}
      </div>
    </nav>
  );
}

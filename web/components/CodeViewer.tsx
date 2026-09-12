"use client";

import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

import type { FileState, RoomState } from "@/lib/roomReducer";
import { userOfAgent } from "@/lib/roomReducer";
import { PROVIDER_COLOR, ProviderDot, clock } from "@/components/ui";

SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("markdown", markdown);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("tsx", tsx);

const SUPPORTED = new Set(["python", "markdown", "json", "bash", "tsx"]);

export function CodeViewer({ state, path }: { state: RoomState; path: string | null }) {
  const file: FileState | undefined = path ? state.files[path] : undefined;

  if (!file) {
    return (
      <div className="flex h-full items-center justify-center bg-ide-bg">
        <p className="text-[12px] text-ide-faint">Select a file</p>
      </div>
    );
  }

  const writer = file.last_written_by ? userOfAgent(state, file.last_written_by) : undefined;
  const writerColor = writer?.provider ? PROVIDER_COLOR[writer.provider] : undefined;
  const lastRejection = file.rejected_by.at(-1);
  const language = SUPPORTED.has(file.language) ? file.language : "text";

  return (
    <div className="flex h-full min-w-0 flex-col bg-ide-bg">
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-ide-border px-3">
        <span className="truncate font-mono text-[11px] text-ide-dim">{file.path}</span>
        <span className="font-mono text-[10px] text-ide-faint">read-only</span>
        {file.last_written_by && (
          <span className="ml-auto flex shrink-0 items-center gap-1.5 font-mono text-[10px]">
            <ProviderDot provider={writer?.provider} size={6} />
            <span style={{ color: writerColor }}>{file.last_written_by}</span>
            <span className="text-ide-faint">
              wrote {file.written_at ? clock(file.written_at) : ""}
            </span>
          </span>
        )}
      </header>

      {lastRejection && (
        <div
          className="shrink-0 border-b px-3 py-1.5 font-mono text-[11px]"
          style={{
            borderColor: "var(--color-ide-border)",
            background: "color-mix(in srgb, var(--color-ide-blocking) 12%, transparent)",
            color: "var(--color-ide-blocking)",
          }}
        >
          ✕ {lastRejection.agent_id} tried to write this file and was refused — {lastRejection.reason}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        <SyntaxHighlighter
          language={language}
          style={vscDarkPlus}
          showLineNumbers
          wrapLongLines={false}
          customStyle={{
            margin: 0,
            padding: "10px 0",
            background: "transparent",
            fontSize: 12,
            lineHeight: 1.55,
            minHeight: "100%",
          }}
          codeTagProps={{ style: { fontFamily: "var(--font-mono)" } }}
          lineNumberStyle={{
            minWidth: "2.6em",
            paddingRight: "1.2em",
            color: "#5a5a5a",
            userSelect: "none",
          }}
        >
          {file.content.length ? file.content : "\n"}
        </SyntaxHighlighter>
      </div>
    </div>
  );
}

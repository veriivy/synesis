"use client";

import { useEffect, useRef, useState } from "react";
import { type ChatItem, type RoomState, participantById } from "@/lib/roomReducer";
import { AnalysisCard } from "@/components/chat/AnalysisCard";
import { PlanCard } from "@/components/chat/PlanCard";
import { PoACard } from "@/components/chat/PoACard";
import { TicketsCard } from "@/components/chat/TicketsCard";
import {
  AgentMessage,
  FileWrittenRow,
  ModeratorMessage,
  RoundDivider,
  SystemRow,
  TICKET_STATUS_COLOR,
  UserMessage,
} from "@/components/chat/Messages";
import { Card, CardHeader, clock, PROVIDER_LABEL } from "@/components/ui";

function ContextCard({ version, content, ts }: { version: number; content: string; ts: string }) {
  return (
    <div className="px-4 py-2">
      <Card>
        <CardHeader>
          <span className="font-mono text-[10px] tracking-widest text-ide-dim uppercase">
            shared context
          </span>
          <span className="font-mono text-[10px] text-ide-faint">v{version}</span>
          <span className="ml-auto font-mono text-[10px] text-ide-faint">{clock(ts)}</span>
        </CardHeader>
        <p className="p-3 text-[12px] leading-relaxed text-ide-dim">{content}</p>
      </Card>
    </div>
  );
}

/** One ordered array of heterogeneous items: every kind is a render case here. */
function Item({
  state,
  item,
  onApprove,
  onSelectFile,
}: {
  state: RoomState;
  item: ChatItem;
  onApprove?: (userId: string, approved: boolean) => void;
  onSelectFile?: (path: string) => void;
}) {
  switch (item.kind) {
    case "user":
      return (
        <UserMessage state={state} userId={item.userId} content={item.content} ts={item.ts} />
      );
    case "agent":
      return (
        <AgentMessage
          state={state}
          agentId={item.agentId}
          content={item.content}
          addressesIssues={item.addressesIssues}
          ts={item.ts}
        />
      );
    case "moderator":
      return <ModeratorMessage content={item.content} round={item.round} ts={item.ts} />;
    case "analysis":
      return <AnalysisCard state={state} analysis={item.analysis} ts={item.ts} />;
    case "plan":
      return <PlanCard state={state} ts={item.ts} onApprove={onApprove} />;
    case "poa":
      return <PoACard state={state} poa={item.poa} ts={item.ts} />;
    case "tickets":
      return <TicketsCard state={state} ts={item.ts} onSelectFile={onSelectFile} />;
    case "participant": {
      const p = participantById(state, item.userId);
      return (
        <SystemRow ts={item.ts}>
          {p?.display_name ?? item.userId} joined
          {p?.provider ? ` with ${PROVIDER_LABEL[p.provider]}` : ""}
          {p?.model ? ` (${p.model})` : ""}
        </SystemRow>
      );
    }
    case "roundComplete":
      return <RoundDivider round={item.round} />;
    case "approval": {
      const name = participantById(state, item.userId)?.display_name ?? item.userId;
      return (
        <SystemRow
          ts={item.ts}
          color={item.approved ? "var(--color-ide-ok)" : "var(--color-ide-minor)"}
        >
          {name} {item.approved ? "approved the plan" : "requested changes"}
        </SystemRow>
      );
    }
    case "planApproved":
      return (
        <SystemRow ts={item.ts} color="var(--color-ide-ok)">
          {item.planId} approved by every user — plan is now binding
        </SystemRow>
      );
    case "context":
      return <ContextCard version={item.version} content={item.content} ts={item.ts} />;
    case "ticketStarted":
      return (
        <SystemRow ts={item.ts} color="var(--color-ide-accent)">
          {item.ticketId} started · {item.agentId}
        </SystemRow>
      );
    case "ticketCompleted":
      return (
        <SystemRow ts={item.ts} color={TICKET_STATUS_COLOR[item.status]}>
          {item.ticketId} {item.status}
        </SystemRow>
      );
    case "write":
      return <FileWrittenRow write={item.write} />;
    case "error":
      return (
        <SystemRow ts={item.ts} color="var(--color-ide-blocking)">
          error in {item.where}: {item.detail}
        </SystemRow>
      );
  }
}

export function ChatPanel({
  state,
  onApprove,
  onSelectFile,
  onSend,
}: {
  state: RoomState;
  onApprove?: (userId: string, approved: boolean) => void;
  onSelectFile?: (path: string) => void;
  onSend?: (content: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState("");
  const pinned = useRef(true);

  // Follow the tail unless the reader has scrolled up to read something.
  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [state.messages.length]);

  function onScroll() {
    const el = scroller.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  function send() {
    const content = draft.trim();
    if (!content) return;
    onSend?.(content);
    setDraft("");
    pinned.current = true;
  }

  return (
    <section className="flex h-full min-w-0 flex-col bg-ide-bg">
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-ide-border px-4">
        <span className="font-mono text-[10px] tracking-widest text-ide-faint uppercase">
          negotiation
        </span>
        {state.currentRound > 0 && (
          <span className="font-mono text-[10px] text-ide-faint">
            round {state.currentRound} / 3
          </span>
        )}
        <span className="ml-auto font-mono text-[10px] text-ide-faint">
          {state.messages.length} events
        </span>
      </header>

      <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto py-2">
        {state.messages.length === 0 && (
          <p className="px-4 py-6 text-[12px] text-ide-faint">
            Waiting for the room to fill. Participants, plans of action, and the K2 diff land here.
          </p>
        )}
        {state.messages.map((item) => (
          <Item
            key={item.id}
            state={state}
            item={item}
            onApprove={onApprove}
            onSelectFile={onSelectFile}
          />
        ))}
      </div>

      <footer className="shrink-0 border-t border-ide-border p-3">
        <div className="flex items-end gap-2 rounded border border-ide-border bg-ide-panel px-2.5 py-2 focus-within:border-ide-accent">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}
            placeholder="Interject — your agent gets this before the next round"
            className="max-h-28 min-h-[20px] flex-1 resize-none bg-transparent text-[13px] text-ide-text outline-none placeholder:text-ide-faint"
          />
          <button
            type="button"
            onClick={send}
            disabled={!draft.trim()}
            className="rounded-sm px-2.5 py-1 text-[12px] text-white disabled:opacity-30"
            style={{ background: "var(--color-ide-accent)" }}
          >
            Send
          </button>
        </div>
      </footer>
    </section>
  );
}

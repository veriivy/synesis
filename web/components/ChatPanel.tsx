"use client";

import { useEffect, useRef, useState } from "react";
import { analysisForRound, type ChatItem, type RoomState } from "@/lib/roomReducer";
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

function Item({
  state,
  item,
  onApprove,
  onSelectFile,
}: {
  state: RoomState;
  item: ChatItem;
  onApprove?: (user_id: string, approved: boolean) => void;
  onSelectFile?: (path: string) => void;
}) {
  switch (item.kind) {
    case "participant_joined": {
      const p = state.participants[item.user_id];
      return (
        <SystemRow ts={item.ts}>
          {p?.display_name ?? item.user_id} joined
          {p?.provider ? ` with ${PROVIDER_LABEL[p.provider]}` : ""}
          {p?.model ? ` (${p.model})` : ""}
        </SystemRow>
      );
    }
    case "poa":
      return <PoACard state={state} agent_id={item.agent_id} ts={item.ts} />;
    case "analysis": {
      const analysis = analysisForRound(state, item.round);
      return analysis ? <AnalysisCard state={state} analysis={analysis} ts={item.ts} /> : null;
    }
    case "agent_message":
      return (
        <AgentMessage
          state={state}
          agent_id={item.agent_id}
          content={item.content}
          addresses_issues={item.addresses_issues}
          ts={item.ts}
        />
      );
    case "user_message":
      return (
        <UserMessage state={state} user_id={item.user_id} content={item.content} ts={item.ts} />
      );
    case "moderator_message":
      return <ModeratorMessage content={item.content} round={item.round} ts={item.ts} />;
    case "round_complete":
      return <RoundDivider round={item.round} />;
    case "plan":
      return <PlanCard state={state} ts={item.ts} onApprove={onApprove} />;
    case "approval": {
      const name = state.participants[item.user_id]?.display_name ?? item.user_id;
      return (
        <SystemRow
          ts={item.ts}
          color={item.approved ? "var(--color-ide-ok)" : "var(--color-ide-minor)"}
        >
          {name} {item.approved ? "approved the plan" : "requested changes"}
        </SystemRow>
      );
    }
    case "plan_approved":
      return (
        <SystemRow ts={item.ts} color="var(--color-ide-ok)">
          {item.plan_id} approved by every user — plan is now binding
        </SystemRow>
      );
    case "context_updated":
      return <ContextCard version={item.version} content={item.content} ts={item.ts} />;
    case "tickets":
      return <TicketsCard state={state} ts={item.ts} onSelectFile={onSelectFile} />;
    case "ticket_started":
      return (
        <SystemRow ts={item.ts} color="var(--color-ide-accent)">
          {item.ticket_id} started · {item.agent_id}
        </SystemRow>
      );
    case "ticket_completed":
      return (
        <SystemRow ts={item.ts} color={TICKET_STATUS_COLOR[item.status]}>
          {item.ticket_id} {item.status}
        </SystemRow>
      );
    case "file_written":
      return (
        <FileWrittenRow
          path={item.path}
          agent_id={item.agent_id}
          ticket_id={item.ticket_id}
          accepted={item.accepted}
          reason={item.reason}
          ts={item.ts}
        />
      );
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
  onApprove?: (user_id: string, approved: boolean) => void;
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
  }, [state.items.length]);

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
        {state.round > 0 && (
          <span className="font-mono text-[10px] text-ide-faint">round {state.round} / 3</span>
        )}
        <span className="ml-auto font-mono text-[10px] text-ide-faint">
          {state.items.length} events
        </span>
      </header>

      <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto py-2">
        {state.items.length === 0 && (
          <p className="px-4 py-6 text-[12px] text-ide-faint">
            Waiting for the room to fill. Participants, plans of action, and the K2 diff land here.
          </p>
        )}
        {state.items.map((item) => (
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

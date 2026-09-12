import type { TicketStatus } from "@/lib/contract";
import { type RoomState, userOfAgent } from "@/lib/roomReducer";
import { clock, PROVIDER_COLOR, PROVIDER_LABEL, ProviderDot, Tag } from "@/components/ui";

/* --------------------------------- user ------------------------------------ */

export function UserMessage({
  state,
  user_id,
  content,
  ts,
}: {
  state: RoomState;
  user_id: string;
  content: string;
  ts: string;
}) {
  const p = state.participants[user_id];
  const name = p?.display_name ?? user_id;
  return (
    <div className="flex gap-2.5 px-4 py-2">
      <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-sm bg-ide-active font-mono text-[10px] text-ide-dim">
        {name.slice(0, 1).toUpperCase()}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[12px] font-medium text-ide-text">{name}</span>
          {p?.is_local && <span className="font-mono text-[10px] text-ide-faint">you</span>}
          <span className="font-mono text-[10px] text-ide-faint">{clock(ts)}</span>
        </div>
        <p className="mt-0.5 text-[13px] leading-relaxed whitespace-pre-wrap text-ide-text">
          {content}
        </p>
      </div>
    </div>
  );
}

/* --------------------------------- agent ----------------------------------- */

export function AgentMessage({
  state,
  agent_id,
  content,
  addresses_issues,
  ts,
}: {
  state: RoomState;
  agent_id: string;
  content: string;
  addresses_issues: string[];
  ts: string;
}) {
  const owner = userOfAgent(state, agent_id);
  const provider = owner?.provider;
  const color = provider ? PROVIDER_COLOR[provider] : "var(--color-ide-faint)";

  return (
    <div className="px-4 py-1.5">
      <div
        className="rounded-sm border-l-2 bg-ide-panel/70 py-2 pr-3 pl-3"
        style={{ borderColor: color }}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <ProviderDot provider={provider} />
          <span className="font-mono text-[11px] text-ide-text">{agent_id}</span>
          <span className="text-[11px] text-ide-faint">
            advocate for {owner?.display_name ?? "—"}
          </span>
          {provider && (
            <span className="font-mono text-[10px]" style={{ color }}>
              {PROVIDER_LABEL[provider]}
            </span>
          )}
          <span className="ml-auto flex items-center gap-1">
            {addresses_issues.map((id) => (
              <Tag key={id} title={`addresses ${id}`}>
                {id}
              </Tag>
            ))}
            <span className="ml-1 font-mono text-[10px] text-ide-faint">{clock(ts)}</span>
          </span>
        </div>
        <p className="mt-1.5 text-[13px] leading-relaxed whitespace-pre-wrap text-ide-text">
          {content}
        </p>
      </div>
    </div>
  );
}

/* ------------------------------- moderator --------------------------------- */

export function ModeratorMessage({
  content,
  round,
  ts,
}: {
  content: string;
  round: number;
  ts: string;
}) {
  return (
    <div className="px-4 py-1.5">
      <div
        className="rounded border-l-2 px-3 py-2.5"
        style={{
          borderColor: "var(--color-ide-k2)",
          background: "color-mix(in srgb, var(--color-ide-k2) 8%, var(--color-ide-panel))",
        }}
      >
        <div className="flex items-center gap-2">
          <span
            className="font-mono text-[10px] font-semibold tracking-widest uppercase"
            style={{ color: "var(--color-ide-k2)" }}
          >
            K2 · moderator
          </span>
          <span className="font-mono text-[10px] text-ide-faint">round {round}</span>
          <span className="ml-auto font-mono text-[10px] text-ide-faint">{clock(ts)}</span>
        </div>
        <p className="mt-1.5 text-[13px] leading-relaxed whitespace-pre-wrap text-ide-text">
          {content}
        </p>
      </div>
    </div>
  );
}

/* --------------------------------- system ---------------------------------- */

export function SystemRow({
  children,
  ts,
  color,
}: {
  children: React.ReactNode;
  ts?: string;
  color?: string;
}) {
  return (
    <div className="flex items-center gap-2 px-4 py-1 font-mono text-[11px] text-ide-faint">
      <span className="shrink-0" style={color ? { color } : undefined}>
        ·
      </span>
      <span className="min-w-0 flex-1 truncate" style={color ? { color } : undefined}>
        {children}
      </span>
      {ts && <span className="shrink-0">{clock(ts)}</span>}
    </div>
  );
}

export function RoundDivider({ round }: { round: number }) {
  return (
    <div className="flex items-center gap-3 px-4 py-3">
      <div className="h-px flex-1 bg-ide-border" />
      <span className="font-mono text-[10px] tracking-widest text-ide-faint uppercase">
        round {round} complete
      </span>
      <div className="h-px flex-1 bg-ide-border" />
    </div>
  );
}

/* ------------------------------ file writes -------------------------------- */

export function FileWrittenRow({
  path,
  agent_id,
  ticket_id,
  accepted,
  reason,
  ts,
}: {
  path: string;
  agent_id: string;
  ticket_id: string;
  accepted: boolean;
  reason: string | null;
  ts: string;
}) {
  if (accepted) {
    return (
      <div className="flex items-center gap-2 px-4 py-1 font-mono text-[11px]">
        <span style={{ color: "var(--color-ide-ok)" }}>✓</span>
        <span className="text-ide-dim">{path}</span>
        <span className="text-ide-faint">
          written by {agent_id} · {ticket_id}
        </span>
        <span className="ml-auto text-ide-faint">{clock(ts)}</span>
      </div>
    );
  }
  return (
    <div className="px-4 py-1.5">
      <div
        className="rounded-sm border-l-2 px-3 py-2"
        style={{
          borderColor: "var(--color-ide-blocking)",
          background: "color-mix(in srgb, var(--color-ide-blocking) 10%, var(--color-ide-panel))",
        }}
      >
        <div className="flex items-center gap-2 font-mono text-[11px]">
          <span style={{ color: "var(--color-ide-blocking)" }}>✕ write rejected</span>
          <span className="text-ide-dim">{path}</span>
          <span className="ml-auto text-ide-faint">{clock(ts)}</span>
        </div>
        <p className="mt-1 font-mono text-[11px] leading-relaxed text-ide-dim">
          {agent_id} · {ticket_id} — {reason}
        </p>
      </div>
    </div>
  );
}

export const TICKET_STATUS_COLOR: Record<TicketStatus, string> = {
  pending: "var(--color-ide-faint)",
  running: "var(--color-ide-accent)",
  done: "var(--color-ide-ok)",
  failed: "var(--color-ide-blocking)",
};

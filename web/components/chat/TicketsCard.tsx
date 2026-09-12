import type { Ticket } from "@/lib/contract";
import { type RoomState, userOfAgent } from "@/lib/roomReducer";
import { TICKET_STATUS_COLOR } from "@/components/chat/Messages";
import { Card, CardHeader, clock, PROVIDER_COLOR, ProviderDot, Tag } from "@/components/ui";

/** The same disjointness assertion the orchestrator runs, shown to the room. */
export function parallelOverlap(tickets: Ticket[]): { a: Ticket; b: Ticket; paths: string[] }[] {
  const parallel = tickets.filter((t) => t.lane === "parallel");
  const clashes: { a: Ticket; b: Ticket; paths: string[] }[] = [];
  for (let i = 0; i < parallel.length; i++) {
    for (let j = i + 1; j < parallel.length; j++) {
      const shared = parallel[i].files_owned.filter((p) => parallel[j].files_owned.includes(p));
      if (shared.length) clashes.push({ a: parallel[i], b: parallel[j], paths: shared });
    }
  }
  return clashes;
}

export function TicketsCard({
  state,
  ts,
  onSelectFile,
}: {
  state: RoomState;
  ts: string;
  onSelectFile?: (path: string) => void;
}) {
  if (state.tickets.length === 0) return null;
  const clashes = parallelOverlap(state.tickets);
  const parallelCount = state.tickets.filter((t) => t.lane === "parallel").length;

  return (
    <div className="px-4 py-2">
      <Card>
        <CardHeader>
          <span className="font-mono text-[10px] tracking-widest text-ide-dim uppercase">
            tickets · {state.tickets.length}
          </span>
          <span className="font-mono text-[10px] text-ide-faint">
            {parallelCount} parallel · {state.tickets.length - parallelCount} sequential
          </span>
          <span className="ml-auto font-mono text-[10px] text-ide-faint">{clock(ts)}</span>
        </CardHeader>

        <ul className="divide-y divide-ide-border">
          {state.tickets.map((t) => {
            const owner = userOfAgent(state, t.assigned_agent);
            const color = owner?.provider ? PROVIDER_COLOR[owner.provider] : undefined;
            return (
              <li key={t.ticket_id} className="px-3 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[11px] text-ide-faint">{t.ticket_id}</span>
                  <span className="text-[12px] text-ide-text">{t.title}</span>
                  <span className="flex items-center gap-1 font-mono text-[11px]" style={{ color }}>
                    <ProviderDot provider={owner?.provider} size={6} />
                    {t.assigned_agent}
                  </span>
                  <Tag
                    color={
                      t.lane === "parallel" ? "var(--color-ide-accent)" : "var(--color-ide-faint)"
                    }
                    upper
                  >
                    {t.lane}
                  </Tag>
                  <span
                    className="ml-auto font-mono text-[10px] uppercase"
                    style={{ color: TICKET_STATUS_COLOR[t.status] }}
                  >
                    {t.status}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1">
                  <span className="font-mono text-[10px] text-ide-faint">owns</span>
                  {t.files_owned.map((f) => (
                    <button
                      key={f}
                      type="button"
                      onClick={() => onSelectFile?.(f)}
                      className="rounded-sm bg-ide-active px-1.5 py-px font-mono text-[10px] text-ide-dim hover:text-ide-text"
                    >
                      {f}
                    </button>
                  ))}
                  {t.depends_on.length > 0 && (
                    <span className="ml-2 font-mono text-[10px] text-ide-faint">
                      depends on {t.depends_on.join(", ")}
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>

        <div
          className="border-t px-3 py-2 font-mono text-[11px]"
          style={{
            borderColor: "var(--color-ide-border)",
            color: clashes.length ? "var(--color-ide-blocking)" : "var(--color-ide-ok)",
          }}
        >
          {clashes.length === 0
            ? "✓ no two parallel tickets share a path in files_owned — asserted, not asked for"
            : clashes
                .map((c) => `✕ ${c.a.ticket_id} and ${c.b.ticket_id} both own ${c.paths.join(", ")}`)
                .join(" · ")}
        </div>
      </Card>
    </div>
  );
}

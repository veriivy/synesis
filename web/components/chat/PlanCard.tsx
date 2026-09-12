import type { RoomState } from "@/lib/roomReducer";
import { Card, CardHeader, clock, Tag } from "@/components/ui";

const K2 = "var(--color-ide-k2)";

/**
 * The approval gate, rendered inline in the chat rather than as a fourth panel.
 * Reads approvals live off RoomState, so approval_updated events move it.
 */
export function PlanCard({
  state,
  ts,
  onApprove,
}: {
  state: RoomState;
  ts: string;
  onApprove?: (user_id: string, approved: boolean) => void;
}) {
  const plan = state.plan;
  if (!plan) return null;

  const voters = Object.keys(plan.approvals);
  const approvedCount = voters.filter((u) => state.approvals[u]).length;
  const local = Object.values(state.participants).find((p) => p.is_local);
  const localPending = local ? !state.approvals[local.user_id] : false;
  const fullyApproved = plan.status === "approved" || (voters.length > 0 && approvedCount === voters.length);

  return (
    <div className="px-4 py-2">
      <Card accent={fullyApproved ? "var(--color-ide-ok)" : K2}>
        <CardHeader accent={fullyApproved ? "var(--color-ide-ok)" : K2}>
          <span
            className="font-mono text-[10px] font-semibold tracking-widest uppercase"
            style={{ color: fullyApproved ? "var(--color-ide-ok)" : K2 }}
          >
            final plan
          </span>
          <span className="font-mono text-[10px] text-ide-faint">{plan.plan_id}</span>
          <span className="font-mono text-[10px] text-ide-faint">
            {plan.rounds_used} rounds
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            <Tag color={fullyApproved ? "var(--color-ide-ok)" : "var(--color-ide-minor)"} upper>
              {fullyApproved ? "approved" : plan.status}
            </Tag>
            <span className="font-mono text-[10px] text-ide-faint">{clock(ts)}</span>
          </span>
        </CardHeader>

        <div className="p-3">
          <p className="text-[13px] leading-relaxed text-ide-text">{plan.summary}</p>

          <div className="mt-3">
            <div className="mb-1.5 font-mono text-[10px] tracking-wider text-ide-faint uppercase">
              steps · {plan.steps.length}
            </div>
            <ol className="grid gap-1.5">
              {plan.steps.map((s, i) => (
                <li key={s.step_id} className="border-l border-ide-border pl-2.5">
                  <div className="flex items-baseline gap-2">
                    <span className="font-mono text-[10px] text-ide-faint">{i + 1}</span>
                    <span className="text-[12px] text-ide-text">{s.title}</span>
                    <span className="ml-1 flex flex-wrap gap-1">
                      {s.files_touched.map((f) => (
                        <span
                          key={f}
                          className="rounded-sm bg-ide-active px-1.5 py-px font-mono text-[10px] text-ide-dim"
                        >
                          {f}
                        </span>
                      ))}
                    </span>
                  </div>
                  <div className="text-[12px] leading-relaxed text-ide-dim">{s.description}</div>
                </li>
              ))}
            </ol>
          </div>

          <div className="mt-3">
            <div className="mb-1.5 font-mono text-[10px] tracking-wider text-ide-faint uppercase">
              resolutions · {plan.resolutions.length}
            </div>
            <ul className="grid gap-1.5">
              {plan.resolutions.map((r) => (
                <li key={r.issue_id} className="flex gap-2">
                  <span className="shrink-0">
                    <Tag>{r.issue_id}</Tag>
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="text-[12px] text-ide-text">{r.outcome}</span>{" "}
                    <span className="text-[12px] text-ide-faint">— {r.rationale}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {/* approval gate */}
          <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-ide-border pt-3">
            <span className="font-mono text-[10px] tracking-wider text-ide-faint uppercase">
              approvals {approvedCount}/{voters.length}
            </span>
            <div className="flex flex-wrap items-center gap-2">
              {voters.map((u) => {
                const ok = state.approvals[u];
                const p = state.participants[u];
                return (
                  <span
                    key={u}
                    className="flex items-center gap-1.5 rounded-sm border px-2 py-1 font-mono text-[11px]"
                    style={{
                      borderColor: ok ? "var(--color-ide-ok)55" : "var(--color-ide-border)",
                      color: ok ? "var(--color-ide-ok)" : "var(--color-ide-dim)",
                    }}
                  >
                    {ok ? "✓" : "○"} {p?.display_name ?? u}
                  </span>
                );
              })}
            </div>

            {local && localPending && onApprove && (
              <div className="ml-auto flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => onApprove(local.user_id, false)}
                  className="rounded-sm border border-ide-border px-2.5 py-1 text-[12px] text-ide-dim hover:bg-ide-hover"
                >
                  Request changes
                </button>
                <button
                  type="button"
                  onClick={() => onApprove(local.user_id, true)}
                  className="rounded-sm px-3 py-1 text-[12px] font-medium text-white"
                  style={{ background: "var(--color-ide-accent)" }}
                >
                  Approve plan
                </button>
              </div>
            )}
            {fullyApproved && (
              <span
                className="ml-auto font-mono text-[11px]"
                style={{ color: "var(--color-ide-ok)" }}
              >
                both users approved — decomposing into tickets
              </span>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}

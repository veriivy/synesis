import type { Analysis, Difference } from "@/lib/types";
import { type RoomState, blockingCount, participantOfAgent } from "@/lib/roomReducer";
import { Card, CardHeader, clock, PROVIDER_COLOR, ProviderDot, Tag } from "@/components/ui";

const K2 = "var(--color-ide-k2)";

function severityColor(d: Difference) {
  return d.severity === "blocking" ? "var(--color-ide-blocking)" : "var(--color-ide-minor)";
}

function Position({
  state,
  agent_id,
  text,
}: {
  state: RoomState;
  agent_id: string;
  text: string;
}) {
  const owner = participantOfAgent(state, agent_id);
  const color = owner?.provider ? PROVIDER_COLOR[owner.provider] : undefined;
  return (
    <div className="flex gap-2">
      <span className="flex w-24 shrink-0 items-center gap-1.5 font-mono text-[11px]">
        <ProviderDot provider={owner?.provider} size={6} />
        <span style={{ color }}>{agent_id}</span>
      </span>
      <span className="min-w-0 flex-1 text-[12px] leading-relaxed text-ide-dim">{text}</span>
    </div>
  );
}

export function AnalysisCard({
  state,
  analysis,
  ts,
}: {
  state: RoomState;
  analysis: Analysis;
  ts: string;
}) {
  const blocking = blockingCount(analysis);

  return (
    <div className="px-4 py-2">
      <Card accent={K2}>
        <CardHeader accent={K2}>
          <span
            className="font-mono text-[10px] font-semibold tracking-widest uppercase"
            style={{ color: K2 }}
          >
            K2 · structural diff
          </span>
          <span className="font-mono text-[10px] text-ide-faint">round {analysis.round}</span>
          <span className="ml-auto flex items-center gap-1.5">
            {analysis.converged ? (
              <Tag color="var(--color-ide-ok)" upper>converged</Tag>
            ) : (
              <Tag color="var(--color-ide-blocking)" upper>
                {blocking} blocking
              </Tag>
            )}
            <span className="font-mono text-[10px] text-ide-faint">{clock(ts)}</span>
          </span>
        </CardHeader>

        <div className="grid gap-3 p-3">
          <section>
            <div className="mb-1.5 font-mono text-[10px] tracking-wider text-ide-faint uppercase">
              similarities · {analysis.similarities.length}
            </div>
            <ul className="grid gap-1.5">
              {analysis.similarities.map((s, i) => (
                <li
                  key={i}
                  className="border-l-2 pl-2.5"
                  style={{ borderColor: "var(--color-ide-ok)" }}
                >
                  <div className="text-[12px] text-ide-text">{s.topic}</div>
                  <div className="text-[12px] leading-relaxed text-ide-dim">{s.detail}</div>
                </li>
              ))}
              {analysis.similarities.length === 0 && (
                <li className="text-[12px] text-ide-faint">none</li>
              )}
            </ul>
          </section>

          <section>
            <div className="mb-1.5 font-mono text-[10px] tracking-wider text-ide-faint uppercase">
              differences · {analysis.differences.length}
            </div>
            <ul className="grid gap-2">
              {analysis.differences.map((d) => (
                <li
                  key={d.issue_id}
                  className="rounded-sm border-l-2 px-2.5 py-2"
                  style={{
                    borderColor: severityColor(d),
                    background:
                      d.severity === "blocking"
                        ? "color-mix(in srgb, var(--color-ide-blocking) 8%, transparent)"
                        : "transparent",
                  }}
                >
                  <div className="mb-1.5 flex flex-wrap items-center gap-2">
                    <Tag color={severityColor(d)}>{d.issue_id}</Tag>
                    <span className="text-[12px] text-ide-text">{d.topic}</span>
                    <span
                      className="ml-auto font-mono text-[10px] uppercase"
                      style={{ color: severityColor(d) }}
                    >
                      {d.severity}
                    </span>
                  </div>
                  <div className="grid gap-1">
                    {Object.entries(d.positions).map(([agent_id, text]) => (
                      <Position key={agent_id} state={state} agent_id={agent_id} text={text} />
                    ))}
                  </div>
                </li>
              ))}
              {analysis.differences.length === 0 && (
                <li className="text-[12px] text-ide-faint">none</li>
              )}
            </ul>
          </section>
        </div>
      </Card>
    </div>
  );
}

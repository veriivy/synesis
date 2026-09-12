import type { PoA } from "@/lib/types";
import { type RoomState, participantOfAgent } from "@/lib/roomReducer";
import { Card, CardHeader, clock, PROVIDER_COLOR, ProviderDot } from "@/components/ui";

export function PoACard({ state, poa, ts }: { state: RoomState; poa: PoA; ts: string }) {
  const owner = participantOfAgent(state, poa.agent_id);
  const color = owner?.provider ? PROVIDER_COLOR[owner.provider] : "var(--color-ide-border)";

  return (
    <div className="px-4 py-2">
      <Card accent={color}>
        <CardHeader accent={color}>
          <ProviderDot provider={owner?.provider} />
          <span className="font-mono text-[10px] tracking-widest text-ide-dim uppercase">
            plan of action
          </span>
          <span className="font-mono text-[11px]" style={{ color }}>
            {poa.agent_id}
          </span>
          <span className="text-[11px] text-ide-faint">
            for {owner?.display_name ?? poa.user_id}
          </span>
          <span className="ml-auto font-mono text-[10px] text-ide-faint">{clock(ts)}</span>
        </CardHeader>

        <div className="p-3">
          <p className="text-[13px] leading-relaxed text-ide-text">{poa.summary}</p>

          <ol className="mt-2.5 grid gap-2">
            {poa.steps.map((s, i) => (
              <li key={s.step_id} className="border-l border-ide-border pl-2.5">
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-[10px] text-ide-faint">{i + 1}</span>
                  <span className="text-[12px] text-ide-text">{s.title}</span>
                </div>
                <div className="text-[12px] leading-relaxed text-ide-dim">{s.description}</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {s.files_touched.map((f) => (
                    <span
                      key={f}
                      className="rounded-sm bg-ide-active px-1.5 py-px font-mono text-[10px] text-ide-dim"
                    >
                      {f}
                    </span>
                  ))}
                </div>
                {s.rationale && (
                  <div className="mt-1 text-[11px] text-ide-faint italic">{s.rationale}</div>
                )}
              </li>
            ))}
          </ol>

          {poa.assumptions.length > 0 && (
            <div className="mt-2.5">
              <div className="mb-1 font-mono text-[10px] tracking-wider text-ide-faint uppercase">
                assumptions
              </div>
              <ul className="grid gap-0.5">
                {poa.assumptions.map((a, i) => (
                  <li key={i} className="text-[12px] text-ide-dim">
                    — {a}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

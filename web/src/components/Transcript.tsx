/** The negotiation, grouped by round. The centre of the demo's first 80 seconds. */

import { useEffect, useRef } from "react";
import { agentStyle } from "../types";
import type { SynesisEvent } from "../types";

interface Props {
  events: SynesisEvent[];
}

interface Round {
  index: number;
  messages: Extract<SynesisEvent, { type: "agent_message" }>[];
  complete: boolean;
}

function groupByRound(events: SynesisEvent[]): Round[] {
  const rounds = new Map<number, Round>();
  const ensure = (index: number): Round => {
    let round = rounds.get(index);
    if (!round) {
      round = { index, messages: [], complete: false };
      rounds.set(index, round);
    }
    return round;
  };

  for (const event of events) {
    if (event.type === "agent_message") ensure(event.round).messages.push(event);
    if (event.type === "round_complete") ensure(event.round).complete = true;
  }
  return [...rounds.values()].sort((a, b) => a.index - b.index);
}

/** Highlight requirement ids and the three named moves. Both are load-bearing: an
 *  audience skimming a wall of text needs to see that the agents cite requirements and
 *  actually concede things. */
function Annotated({ text }: { text: string }) {
  const parts = text.split(/(\br\d+\b|\bCONCEDE\b|\bHOLD\b|\bCOMPROMISE\b)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (/^r\d+$/.test(part)) {
          return (
            <code
              key={i}
              className="rounded bg-slate-700/70 px-1 py-0.5 font-mono text-[0.75rem] text-amber-200"
            >
              {part}
            </code>
          );
        }
        if (part === "CONCEDE") {
          return (
            <strong key={i} className="font-semibold text-emerald-300">
              {part}
            </strong>
          );
        }
        if (part === "HOLD") {
          return (
            <strong key={i} className="font-semibold text-rose-300">
              {part}
            </strong>
          );
        }
        if (part === "COMPROMISE") {
          return (
            <strong key={i} className="font-semibold text-sky-300">
              {part}
            </strong>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

export default function Transcript({ events }: Props) {
  const rounds = groupByRound(events);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events.length]);

  if (rounds.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-8 text-center text-sm text-slate-500">
        Waiting for both users to submit their requirements. The negotiation starts on
        its own once the second set arrives.
      </div>
    );
  }

  return (
    <div className="space-y-8 p-5">
      {rounds.map((round) => (
        <section key={round.index}>
          <div className="mb-3 flex items-center gap-3">
            <h3 className="text-xs font-semibold uppercase tracking-widest text-slate-500">
              {round.index === 0 ? "Round 0 · opening positions" : `Round ${round.index}`}
            </h3>
            <div className="h-px flex-1 bg-slate-800" />
            {!round.complete && (
              <span className="animate-pulse text-xs text-slate-500">thinking…</span>
            )}
          </div>

          <div className="space-y-4">
            {round.messages.map((message) => {
              const style = agentStyle(message.agent_id);
              return (
                <article
                  key={`${message.agent_id}-${message.round}`}
                  className="animate-slide-in rounded-lg border border-slate-800 bg-slate-900/60 p-4"
                >
                  <header className="mb-2 flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${style.dot}`} />
                    <span className={`text-sm font-semibold ${style.accent}`}>
                      {style.label}
                    </span>
                    <span className="text-xs text-slate-500">
                      advocating for {message.user_id}
                    </span>
                  </header>
                  <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
                    <Annotated text={message.content} />
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      ))}
      <div ref={bottom} />
    </div>
  );
}

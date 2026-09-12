/**
 * Writes and commits, live.
 *
 * Rejections are the demo's money shot — the moment where the audience sees a conflict
 * prevented structurally instead of resolved after the fact. They get a red border, a
 * flash, the rule that fired, and the full reason. Do not make them quieter.
 */

import { agentStyle } from "../types";
import type { SynesisEvent } from "../types";

interface Props {
  events: SynesisEvent[];
}

type Entry =
  | Extract<SynesisEvent, { type: "file_written" }>
  | Extract<SynesisEvent, { type: "commit" }>;

/** The reason is prefixed with the rule that fired: `path_not_owned: ...`. */
function splitReason(reason: string): [string, string] {
  const at = reason.indexOf(":");
  if (at === -1) return ["rejected", reason];
  return [reason.slice(0, at), reason.slice(at + 1).trim()];
}

function RuleBadge({ rule }: { rule: string }) {
  const copy: Record<string, string> = {
    path_not_owned: "not your file",
    path_escape: "escaped the workspace",
    no_approved_plan: "no approved plan",
    protected_path: "git metadata",
    size_limit: "too large",
    io_error: "write failed",
  };
  return (
    <span className="rounded bg-rose-500/20 px-1.5 py-0.5 font-mono text-[0.65rem] text-rose-300">
      {copy[rule] ?? rule}
    </span>
  );
}

export default function WriteLog({ events }: Props) {
  const log = events.filter(
    (e): e is Entry => e.type === "file_written" || e.type === "commit",
  );

  const rejected = log.filter((e) => e.type === "file_written" && !e.accepted).length;

  if (log.length === 0) {
    return (
      <div className="p-5 text-sm text-slate-500">
        Nothing written yet. Once the plan is approved, every write attempt appears here
        — including the ones the workspace refuses.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {rejected > 0 && (
        <div className="border-b border-rose-900/50 bg-rose-950/30 px-5 py-2 text-xs text-rose-300">
          {rejected} write{rejected === 1 ? "" : "s"} refused by the workspace sandbox.
        </div>
      )}

      <ul className="flex-1 space-y-1.5 overflow-y-auto p-4">
        {log.map((event, i) => {
          const style = agentStyle(event.agent_id);

          if (event.type === "commit") {
            return (
              <li
                key={`commit-${event.sha}`}
                className="animate-slide-in rounded border border-slate-700 bg-slate-900 p-3"
              >
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${style.dot}`} />
                  <span className={`text-xs font-semibold ${style.accent}`}>
                    {style.label}
                  </span>
                  <span className="font-mono text-xs text-amber-300">
                    {event.sha.slice(0, 7)}
                  </span>
                  <span className="text-xs text-slate-500">committed</span>
                </div>
                <p className="mt-1 text-sm text-slate-300">{event.message}</p>
                <p className="mt-1 font-mono text-[0.7rem] text-slate-600">
                  {event.files.join(", ")}
                </p>
              </li>
            );
          }

          if (event.accepted) {
            return (
              <li
                key={`w-${i}`}
                className="animate-slide-in flex items-center gap-2 rounded border border-slate-800/70 bg-slate-900/40 px-3 py-2"
              >
                <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
                <span className={`text-xs ${style.accent}`}>{style.label}</span>
                <span className="flex-1 truncate font-mono text-xs text-slate-300">
                  {event.path}
                </span>
                <span className="font-mono text-[0.7rem] text-slate-600">
                  {event.bytes}b
                </span>
              </li>
            );
          }

          const [rule, detail] = splitReason(event.reason ?? "");
          return (
            <li
              key={`w-${i}`}
              className="animate-reject animate-slide-in rounded border-2 border-rose-500/60 bg-rose-950/40 p-3"
            >
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <span className={`text-xs font-semibold ${style.accent}`}>
                  {style.label}
                </span>
                <span className="text-xs font-semibold uppercase tracking-wide text-rose-300">
                  refused
                </span>
                <RuleBadge rule={rule} />
              </div>
              <p className="mb-1 font-mono text-xs text-rose-200">{event.path}</p>
              <p className="text-xs leading-relaxed text-rose-100/70">{detail}</p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

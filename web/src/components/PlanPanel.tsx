"use client";

/**
 * The workplan and the approval gate.
 *
 * Concessions are shown first and largest. A plan where nobody gave anything up is a
 * failed negotiation, so the UI puts the evidence of real trade-offs where it cannot
 * be missed.
 */

import { useState } from "react";
import { agentStyle } from "@/lib/types";
import type { UnresolvedIssue, Workplan } from "@/lib/types";

interface Props {
  plan: Workplan | null;
  deadlock: UnresolvedIssue[];
  phase: string;
  onApprove: (approved: boolean, notes?: string) => void;
  busy?: boolean;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-slate-800 px-5 py-4">
      <h4 className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">
        {title}
      </h4>
      {children}
    </section>
  );
}

function Deadlock({ issues }: { issues: UnresolvedIssue[] }) {
  return (
    <div className="m-4 rounded-lg border border-amber-500/40 bg-amber-950/30 p-4">
      <h3 className="mb-1 text-sm font-semibold text-amber-300">
        Deadlock — escalated to you
      </h3>
      <p className="mb-4 text-xs leading-relaxed text-amber-100/70">
        Two must-have requirements are irreconcilable, and neither agent is permitted to
        concede one on its user&apos;s behalf. This is a correct outcome: the
        disagreement surfaced now, in a paragraph, rather than at merge time in a diff.
      </p>
      {issues.map((issue, i) => (
        <div key={i} className="mb-3 last:mb-0">
          <p className="mb-2 text-sm text-slate-200">{issue.issue}</p>
          <div className="space-y-2">
            {Object.entries(issue.positions).map(([agent, position]) => {
              const style = agentStyle(agent);
              return (
                <div key={agent} className="rounded border border-slate-800 bg-slate-900/60 p-3">
                  <div className={`mb-1 text-xs font-semibold ${style.accent}`}>
                    {style.label}
                  </div>
                  <p className="text-xs leading-relaxed text-slate-400">{position}</p>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function PlanPanel({ plan, deadlock, phase, onApprove, busy }: Props) {
  const [notes, setNotes] = useState("");

  if (deadlock.length > 0) return <Deadlock issues={deadlock} />;

  if (!plan) {
    return (
      <div className="p-6 text-sm text-slate-500">
        No plan yet. The moderator emits one once the agents have resolved every
        conflicting requirement — or a deadlock if they cannot.
      </div>
    );
  }

  const awaiting = phase === "awaiting_approval" && plan.status === "proposed";

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <header className="px-5 py-4">
        <div className="flex items-baseline justify-between">
          <h3 className="text-sm font-semibold text-slate-200">Proposed workplan</h3>
          <span className="font-mono text-xs text-slate-500">
            {plan.rounds_used} round{plan.rounds_used === 1 ? "" : "s"}
          </span>
        </div>
        {plan.repo.commit && (
          <p className="mt-1 font-mono text-[0.7rem] text-slate-600">
            against {plan.repo.commit.slice(0, 10)}
          </p>
        )}
      </header>

      <Section title={`Concessions (${plan.concessions.length})`}>
        {plan.concessions.length === 0 ? (
          <p className="rounded border border-rose-500/40 bg-rose-950/30 p-3 text-xs text-rose-200">
            Nobody conceded anything. Either the requirements never actually conflicted,
            or the agents agreed without negotiating — which is the failure this system
            exists to catch.
          </p>
        ) : (
          <ul className="space-y-3">
            {plan.concessions.map((concession, i) => {
              const style = agentStyle(concession.agent);
              return (
                <li
                  key={i}
                  className="rounded-lg border border-slate-800 bg-slate-900/60 p-3"
                >
                  <div className={`mb-2 text-xs font-semibold ${style.accent}`}>
                    {style.label} conceded
                  </div>
                  <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
                    <span className="text-rose-300 line-through">{concession.gave_up}</span>
                    <span className="text-slate-600">→</span>
                    <span className="text-emerald-300">{concession.accepted}</span>
                  </div>
                  <p className="text-xs leading-relaxed text-slate-400">
                    {concession.reason}
                  </p>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      <Section title={`Interface contracts (${plan.interface_contracts.length})`}>
        <ul className="space-y-2">
          {plan.interface_contracts.map((contract) => (
            <li
              key={contract.contract_id}
              className="rounded-lg border border-slate-800 bg-slate-900/60 p-3"
            >
              <div className="mb-1 flex items-center gap-2">
                <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[0.65rem] uppercase text-slate-400">
                  {contract.kind}
                </span>
                <span className="text-xs text-slate-300">{contract.name}</span>
              </div>
              <code className="block break-all font-mono text-xs text-sky-300">
                {contract.signature}
              </code>
              {contract.notes && (
                <p className="mt-2 text-xs leading-relaxed text-slate-500">
                  {contract.notes}
                </p>
              )}
            </li>
          ))}
        </ul>
      </Section>

      <Section title={`Tasks and file ownership (${plan.tasks.length})`}>
        <ul className="space-y-2">
          {plan.tasks.map((task) => {
            const style = agentStyle(task.owner_agent);
            return (
              <li
                key={task.task_id}
                className="rounded-lg border border-slate-800 bg-slate-900/60 p-3"
              >
                <div className="mb-1 flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${style.dot}`} />
                  <span className="font-mono text-xs text-slate-500">{task.task_id}</span>
                  <span className="text-sm text-slate-200">{task.title}</span>
                </div>
                <p className="mb-2 text-xs leading-relaxed text-slate-500">
                  {task.description}
                </p>
                <ul className="space-y-0.5">
                  {task.files_owned.map((path) => (
                    <li key={path} className={`font-mono text-xs ${style.accent}`}>
                      {path}
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      </Section>

      {awaiting && (
        <div className="sticky bottom-0 mt-auto border-t border-slate-800 bg-slate-950/95 p-4 backdrop-blur">
          <p className="mb-3 text-xs leading-relaxed text-slate-400">
            No agent can write a single byte until you approve. The workspace refuses
            every write while this plan is unapproved.
          </p>
          <input
            id="approval-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes (optional)"
            className="mb-3 w-full rounded border border-slate-800 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-slate-600"
          />
          <div className="flex gap-2">
            <button
              disabled={busy}
              onClick={() => onApprove(true, notes)}
              className="flex-1 rounded bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-50"
            >
              Approve and let them write
            </button>
            <button
              disabled={busy}
              onClick={() => onApprove(false, notes)}
              className="rounded border border-slate-700 px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500 disabled:opacity-50"
            >
              Reject
            </button>
          </div>
        </div>
      )}

      {plan.status === "approved" && (
        <div className="border-t border-emerald-900/50 bg-emerald-950/20 px-5 py-3 text-xs text-emerald-300">
          Approved. Agents may now write only the files listed above.
        </div>
      )}
    </div>
  );
}

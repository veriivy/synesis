/**
 * Room creation and requirement intake.
 *
 * Pre-loaded with the demo's two conflicting intent sets, because on stage you have
 * ninety seconds and you are not typing paragraphs. "Load the demo conflict" fills both
 * sides; everything stays editable.
 */

import { useState } from "react";
import { DEMO_INTENTS } from "../demoIntents";
import type { Priority, Requirement } from "../types";
import { agentStyle } from "../types";

interface Props {
  onStart: (
    feature: string,
    participants: {
      user_id: string;
      agent_id: string;
      display_name: string;
      requirements: Requirement[];
    }[],
  ) => void;
  busy?: boolean;
  error?: string | null;
}

type Draft = {
  user_id: string;
  agent_id: string;
  display_name: string;
  requirements: Requirement[];
};

const BLANK: Draft[] = [
  { user_id: "u1", agent_id: "claude", display_name: "", requirements: [] },
  { user_id: "u2", agent_id: "gpt", display_name: "", requirements: [] },
];

export default function Intake({ onStart, busy, error }: Props) {
  const [feature, setFeature] = useState("");
  const [drafts, setDrafts] = useState<Draft[]>(BLANK);

  const loadDemo = () => {
    setFeature(DEMO_INTENTS.feature);
    setDrafts(
      DEMO_INTENTS.participants.map((p) => ({
        user_id: p.user_id,
        agent_id: p.agent_id,
        display_name: p.display_name,
        requirements: p.requirements,
      })),
    );
  };

  const update = (index: number, patch: Partial<Draft>) =>
    setDrafts((prior) => prior.map((d, i) => (i === index ? { ...d, ...patch } : d)));

  const setRequirement = (di: number, ri: number, patch: Partial<Requirement>) =>
    update(di, {
      requirements: drafts[di].requirements.map((r, i) =>
        i === ri ? { ...r, ...patch } : r,
      ),
    });

  const addRequirement = (di: number) =>
    update(di, {
      requirements: [
        ...drafts[di].requirements,
        {
          req_id: `r${drafts[di].requirements.length + 1 + di * 10}`,
          text: "",
          priority: "must-have" as Priority,
        },
      ],
    });

  const ready =
    feature.trim().length > 0 &&
    drafts.every((d) => d.requirements.some((r) => r.text.trim().length > 0));

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold text-slate-100">Start a negotiation</h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-400">
          Each person states what they need. Their agent argues for it. Conflicts get
          resolved before any code is written — or escalated back to you if two
          must-haves genuinely cannot both hold.
        </p>
        <button
          onClick={loadDemo}
          className="mt-4 rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:border-slate-500"
        >
          Load the demo conflict
        </button>
      </header>

      <label className="mb-8 block">
        <span className="mb-2 block text-xs font-semibold uppercase tracking-widest text-slate-500">
          The feature
        </span>
        <input
          value={feature}
          onChange={(e) => setFeature(e.target.value)}
          placeholder="Add user authentication to the notes API…"
          className="w-full rounded border border-slate-800 bg-slate-900 px-4 py-3 text-sm outline-none focus:border-slate-600"
        />
      </label>

      <div className="grid gap-6 md:grid-cols-2">
        {drafts.map((draft, di) => {
          const style = agentStyle(draft.agent_id);
          return (
            <section
              key={draft.user_id}
              className="rounded-lg border border-slate-800 bg-slate-900/50 p-4"
            >
              <header className="mb-3 flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${style.dot}`} />
                <input
                  value={draft.display_name}
                  onChange={(e) => update(di, { display_name: e.target.value })}
                  placeholder={`User ${draft.user_id}`}
                  className="flex-1 bg-transparent text-sm font-semibold text-slate-200 outline-none"
                />
                <span className={`text-xs ${style.accent}`}>{style.label}</span>
              </header>

              <div className="space-y-2">
                {draft.requirements.map((requirement, ri) => (
                  <div key={ri} className="rounded border border-slate-800 bg-slate-950/50 p-2">
                    <div className="mb-1.5 flex items-center gap-2">
                      <code className="font-mono text-[0.7rem] text-amber-200">
                        {requirement.req_id}
                      </code>
                      <select
                        value={requirement.priority}
                        onChange={(e) =>
                          setRequirement(di, ri, {
                            priority: e.target.value as Priority,
                          })
                        }
                        className={`rounded bg-slate-800 px-1.5 py-0.5 text-[0.7rem] outline-none ${
                          requirement.priority === "must-have"
                            ? "text-rose-300"
                            : "text-slate-400"
                        }`}
                      >
                        <option value="must-have">must-have</option>
                        <option value="nice-to-have">nice-to-have</option>
                      </select>
                    </div>
                    <textarea
                      value={requirement.text}
                      onChange={(e) => setRequirement(di, ri, { text: e.target.value })}
                      rows={2}
                      placeholder="What does this person actually need?"
                      className="w-full resize-none bg-transparent text-xs leading-relaxed text-slate-300 outline-none"
                    />
                  </div>
                ))}
              </div>

              <button
                onClick={() => addRequirement(di)}
                className="mt-2 text-xs text-slate-500 transition hover:text-slate-300"
              >
                + add requirement
              </button>
            </section>
          );
        })}
      </div>

      {error && (
        <p className="mt-6 rounded border border-rose-500/40 bg-rose-950/30 p-3 text-sm text-rose-200">
          {error}
        </p>
      )}

      <button
        disabled={!ready || busy}
        onClick={() =>
          onStart(
            feature,
            drafts.map((d) => ({
              ...d,
              requirements: d.requirements.filter((r) => r.text.trim().length > 0),
            })),
          )
        }
        className="mt-8 w-full rounded bg-slate-100 px-4 py-3 text-sm font-semibold text-slate-900 transition hover:bg-white disabled:opacity-40"
      >
        {busy ? "Starting…" : "Send both sets to the agents"}
      </button>
      {!ready && (
        <p className="mt-2 text-center text-xs text-slate-600">
          Both sides need at least one requirement. One agent is not a negotiation.
        </p>
      )}
    </div>
  );
}

/**
 * THE FROZEN CONTRACT, in TypeScript.
 *
 * These types mirror `engine/engine/schemas.py` exactly. `/fixtures` is the same
 * contract as data, and the engine test suite asserts the fixtures validate against
 * the Python models — so if these types are right for the fixtures, they are right for
 * the server.
 *
 * Do not change a field name here without the team agreeing out loud.
 */

export type Priority = "must-have" | "nice-to-have";

export interface Requirement {
  req_id: string;
  text: string;
  priority: Priority;
}

export interface Participant {
  user_id: string;
  agent_id: string;
  provider: string;
  model: string;
  display_name: string;
  requirements: Requirement[];
}

// --- workplan ---------------------------------------------------------------

export interface Task {
  task_id: string;
  title: string;
  description: string;
  owner_agent: string;
  owner_user: string;
  files_owned: string[];
  depends_on: string[];
}

export interface InterfaceContract {
  contract_id: string;
  name: string;
  kind: "function" | "http_endpoint" | "schema";
  signature: string;
  producer_task: string;
  consumer_tasks: string[];
  notes: string;
}

export interface Concession {
  agent: string;
  gave_up: string;
  accepted: string;
  reason: string;
}

export interface UnresolvedIssue {
  issue: string;
  positions: Record<string, string>;
}

export interface Workplan {
  plan_id: string;
  repo: { url: string; commit: string };
  rounds_used: number;
  status: "proposed" | "approved" | "rejected";
  tasks: Task[];
  interface_contracts: InterfaceContract[];
  concessions: Concession[];
  unresolved: UnresolvedIssue[];
}

// --- events -----------------------------------------------------------------

interface EventBase {
  room_id: string;
  ts: string;
}

export interface AgentMessageEvent extends EventBase {
  type: "agent_message";
  round: number;
  agent_id: string;
  user_id: string;
  content: string;
}

export interface RoundCompleteEvent extends EventBase {
  type: "round_complete";
  round: number;
}

export interface PlanProposedEvent extends EventBase {
  type: "plan_proposed";
  plan: Workplan;
}

export interface DeadlockEvent extends EventBase {
  type: "deadlock";
  round: number;
  unresolved: UnresolvedIssue[];
}

/** `accepted: false` carries a non-null `reason` prefixed with the rule that fired. */
export interface FileWrittenEvent extends EventBase {
  type: "file_written";
  agent_id: string;
  path: string;
  bytes: number;
  accepted: boolean;
  reason: string | null;
}

export interface CommitEvent extends EventBase {
  type: "commit";
  agent_id: string;
  sha: string;
  message: string;
  files: string[];
}

/** Not in the frozen contract — server-side plumbing the UI shows rather than hides. */
export interface IntentRegisteredEvent extends EventBase {
  type: "intent_registered";
  user_id: string;
  agent_id: string;
  requirement_count: number;
}

export interface ErrorEvent extends EventBase {
  type: "error";
  message: string;
}

export interface PlanRejectedEvent extends EventBase {
  type: "plan_rejected";
  notes: string | null;
}

export type SynesisEvent =
  | AgentMessageEvent
  | RoundCompleteEvent
  | PlanProposedEvent
  | DeadlockEvent
  | FileWrittenEvent
  | CommitEvent
  | IntentRegisteredEvent
  | ErrorEvent
  | PlanRejectedEvent;

// --- room -------------------------------------------------------------------

export type Phase =
  | "waiting"
  | "negotiating"
  | "awaiting_approval"
  | "deadlocked"
  | "executing"
  | "done"
  | "rejected";

export interface RoomSummary {
  room_id: string;
  phase: Phase;
  feature: string;
  repo: { url: string; commit: string };
  participants: Participant[];
  plan: Workplan | null;
}

export interface FileEntry {
  path: string;
  agent_id: string;
  diff: string;
}

// --- presentation -----------------------------------------------------------

/** Stable per-agent colours. Agents must be visually distinct at a glance on stage. */
export const AGENT_STYLE: Record<string, { label: string; accent: string; dot: string }> = {
  claude: { label: "Claude", accent: "text-orange-300", dot: "bg-orange-400" },
  gpt: { label: "GPT", accent: "text-emerald-300", dot: "bg-emerald-400" },
  gemini: { label: "Gemini", accent: "text-sky-300", dot: "bg-sky-400" },
  k2: { label: "Kimi K2", accent: "text-violet-300", dot: "bg-violet-400" },
  grok: { label: "Grok", accent: "text-rose-300", dot: "bg-rose-400" },
};

export function agentStyle(agentId: string) {
  return (
    AGENT_STYLE[agentId] ?? {
      label: agentId,
      accent: "text-slate-300",
      dot: "bg-slate-400",
    }
  );
}

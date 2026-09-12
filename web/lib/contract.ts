// THE FROZEN CONTRACT, as TypeScript.
// Mirrors CLAUDE.md + /fixtures exactly. Do not change without the team agreeing out loud.

export type Provider = "claude" | "gemini" | "gpt";
export type Priority = "must" | "want";
export type Severity = "blocking" | "minor";
export type Lane = "parallel" | "sequential";
export type TicketStatus = "pending" | "running" | "done" | "failed";
export type PlanStatus = "proposed" | "approved" | "rejected";

/* ---------------------------------- schemas --------------------------------- */

export interface PoAStep {
  step_id: string;
  title: string;
  description: string;
  files_touched: string[];
  rationale: string;
}

export interface PoA {
  poa_id: string;
  agent_id: string;
  user_id: string;
  summary: string;
  steps: PoAStep[];
  assumptions: string[];
}

export interface Similarity {
  topic: string;
  detail: string;
}

export interface Difference {
  issue_id: string;
  topic: string;
  positions: Record<string, string>; // agent_id -> position
  severity: Severity;
}

export interface Analysis {
  round: number;
  similarities: Similarity[];
  differences: Difference[];
  converged: boolean;
}

export interface Resolution {
  issue_id: string;
  outcome: string;
  rationale: string;
}

export interface FinalPlan {
  plan_id: string;
  rounds_used: number;
  summary: string;
  steps: PoAStep[];
  resolutions: Resolution[];
  approvals: Record<string, boolean>; // user_id -> approved
  status: PlanStatus;
}

export interface Ticket {
  ticket_id: string;
  plan_id: string;
  title: string;
  description: string;
  assigned_agent: string;
  files_owned: string[];
  depends_on: string[];
  lane: Lane;
  status: TicketStatus;
}

export interface SharedContext {
  room_id: string;
  version: number;
  content: string;
  updated_at: string;
}

export interface Task {
  text: string;
  priority: Priority;
}

/* ---------------------------------- events ---------------------------------- */

interface EventBase {
  room_id: string;
  ts: string;
}

export type SSEEvent =
  | (EventBase & {
      type: "participant_joined";
      user_id: string;
      provider: Provider;
      model: string;
      /** Optional: not in the orchestrator contract; set by the local setup modal. */
      display_name?: string;
    })
  | (EventBase & { type: "poa_generated"; agent_id: string; user_id: string; poa: PoA })
  | (EventBase & {
      type: "analysis";
      round: number;
      similarities: Similarity[];
      differences: Difference[];
      converged: boolean;
    })
  | (EventBase & {
      type: "agent_message";
      round: number;
      agent_id: string;
      content: string;
      addresses_issues: string[];
    })
  | (EventBase & { type: "user_message"; round: number; user_id: string; content: string })
  | (EventBase & { type: "moderator_message"; round: number; content: string })
  | (EventBase & { type: "round_complete"; round: number })
  | (EventBase & { type: "plan_proposed"; plan: FinalPlan })
  | (EventBase & { type: "approval_updated"; user_id: string; approved: boolean })
  | (EventBase & { type: "plan_approved"; plan_id: string })
  | (EventBase & { type: "context_updated"; version: number; content: string })
  | (EventBase & { type: "tickets_created"; tickets: Ticket[] })
  | (EventBase & { type: "ticket_started"; ticket_id: string; agent_id: string })
  | (EventBase & {
      type: "file_written";
      ticket_id: string;
      agent_id: string;
      path: string;
      accepted: boolean;
      reason: string | null;
    })
  | (EventBase & { type: "ticket_completed"; ticket_id: string; status: TicketStatus })
  | (EventBase & { type: "error"; where: string; detail: string });

export type SSEEventType = SSEEvent["type"];

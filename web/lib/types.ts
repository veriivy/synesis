// THE FROZEN CONTRACT, transcribed. Wire field names are snake_case exactly as the
// orchestrator emits them. Changing anything here requires all three of us to agree.

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

/** One plan of action per agent. */
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
  /** agent_id -> that agent's position. */
  positions: Record<string, string>;
  severity: Severity;
}

/** One K2 analysis per round. */
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
  /** user_id -> approved. */
  approvals: Record<string, boolean>;
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

/**
 * A participant, as POST /rooms/{id}/participants takes them. `agent_id` and
 * `is_local` are learned client-side: the first from poa_generated, the second
 * from whoever this browser is. `api_key` is never stored here.
 */
export interface Participant {
  user_id: string;
  display_name: string;
  provider: Provider;
  model: string;
  agent_id?: string;
  is_local?: boolean;
}

/* ---------------------------------- events ---------------------------------- */

/** Every event carries room_id and ts. */
export interface EventBase {
  room_id: string;
  ts: string;
}

export interface ParticipantJoined extends EventBase {
  type: "participant_joined";
  user_id: string;
  provider: Provider;
  model: string;
  /** Not in the orchestrator contract. Optional, set by the local setup modal. */
  display_name?: string;
}

export interface PoaGenerated extends EventBase {
  type: "poa_generated";
  agent_id: string;
  user_id: string;
  poa: PoA;
}

export interface AnalysisEvent extends EventBase {
  type: "analysis";
  round: number;
  similarities: Similarity[];
  differences: Difference[];
  converged: boolean;
}

export interface AgentMessage extends EventBase {
  type: "agent_message";
  round: number;
  agent_id: string;
  content: string;
  addresses_issues: string[];
}

export interface UserMessage extends EventBase {
  type: "user_message";
  round: number;
  user_id: string;
  content: string;
}

export interface ModeratorMessage extends EventBase {
  type: "moderator_message";
  round: number;
  content: string;
}

export interface RoundComplete extends EventBase {
  type: "round_complete";
  round: number;
}

export interface PlanProposed extends EventBase {
  type: "plan_proposed";
  plan: FinalPlan;
}

export interface ApprovalUpdated extends EventBase {
  type: "approval_updated";
  user_id: string;
  approved: boolean;
}

export interface PlanApproved extends EventBase {
  type: "plan_approved";
  plan_id: string;
}

export interface ContextUpdated extends EventBase {
  type: "context_updated";
  version: number;
  content: string;
}

export interface TicketsCreated extends EventBase {
  type: "tickets_created";
  tickets: Ticket[];
}

export interface TicketStarted extends EventBase {
  type: "ticket_started";
  ticket_id: string;
  agent_id: string;
}

export interface FileWritten extends EventBase {
  type: "file_written";
  ticket_id: string;
  agent_id: string;
  path: string;
  accepted: boolean;
  reason: string | null;
}

export interface TicketCompleted extends EventBase {
  type: "ticket_completed";
  ticket_id: string;
  status: TicketStatus;
}

export interface ErrorEvent extends EventBase {
  type: "error";
  where: string;
  detail: string;
}

export type SSEEvent =
  | ParticipantJoined
  | PoaGenerated
  | AnalysisEvent
  | AgentMessage
  | UserMessage
  | ModeratorMessage
  | RoundComplete
  | PlanProposed
  | ApprovalUpdated
  | PlanApproved
  | ContextUpdated
  | TicketsCreated
  | TicketStarted
  | FileWritten
  | TicketCompleted
  | ErrorEvent;

export type SSEEventType = SSEEvent["type"];

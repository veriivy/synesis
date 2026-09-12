// One realistic instance of every SSE event, in the order a real negotiation
// produces them. This is the demo until the orchestrator exists.
//
// It is the same happy path as /fixtures/stream.jsonl — scripts/check-reducer.ts
// asserts the two match event for event, so a typo here fails the check instead
// of turning into a 2am integration bug.

import type { ErrorEvent, SSEEvent } from "./types";

const ROOM = "room_demo01";

export const fixtureEvents: SSEEvent[] = [
  /* ---- the room fills ---- */
  {
    type: "participant_joined",
    room_id: ROOM,
    user_id: "u1",
    provider: "claude",
    model: "claude-opus-5",
    ts: "2026-09-12T03:00:01.000Z",
  },
  {
    type: "participant_joined",
    room_id: ROOM,
    user_id: "u2",
    provider: "gpt",
    model: "gpt-5",
    ts: "2026-09-12T03:00:02.000Z",
  },

  /* ---- both agents draft in parallel ---- */
  {
    type: "poa_generated",
    room_id: ROOM,
    agent_id: "a1",
    user_id: "u1",
    poa: {
      poa_id: "poa_a1",
      agent_id: "a1",
      user_id: "u1",
      summary: "Cookie-based sessions. No JWT.",
      steps: [
        {
          step_id: "s1",
          title: "Session helpers",
          description: "HttpOnly cookie.",
          files_touched: ["src/auth/session.py"],
          rationale: "u1 must.",
        },
      ],
      assumptions: [],
    },
    ts: "2026-09-12T03:00:20.000Z",
  },
  {
    type: "poa_generated",
    room_id: ROOM,
    agent_id: "a2",
    user_id: "u2",
    poa: {
      poa_id: "poa_a2",
      agent_id: "a2",
      user_id: "u2",
      summary: "Bearer JWT for mobile. No cookies required.",
      steps: [
        {
          step_id: "s1",
          title: "JWT helpers",
          description: "HS256, 15m exp.",
          files_touched: ["src/auth/jwt.py"],
          rationale: "u2 must.",
        },
      ],
      assumptions: [],
    },
    ts: "2026-09-12T03:00:21.000Z",
  },

  /* ---- round 1: K2 diffs the two plans and names the conflicts ---- */
  {
    type: "analysis",
    room_id: ROOM,
    round: 1,
    similarities: [
      {
        topic: "authenticate_user as the gate",
        detail: "Both introduce authenticate_user(...) -> User | None.",
      },
    ],
    differences: [
      {
        issue_id: "d1",
        topic: "Session cookies vs bearer JWT",
        positions: {
          a1: "HttpOnly session cookie (u1 must).",
          a2: "Bearer JWT (u2 must).",
        },
        severity: "blocking",
      },
      {
        issue_id: "d2",
        topic: "Who owns src/auth/middleware.py",
        positions: {
          a1: "Write cookie auth here.",
          a2: "Write header auth here.",
        },
        severity: "blocking",
      },
    ],
    converged: false,
    ts: "2026-09-12T03:00:35.000Z",
  },
  {
    type: "moderator_message",
    room_id: ROOM,
    round: 1,
    content:
      "d1 is two musts. Can authenticate_user accept both? d2: name a single owner for middleware.py.",
    ts: "2026-09-12T03:00:40.000Z",
  },
  {
    type: "agent_message",
    room_id: ROOM,
    round: 1,
    agent_id: "a1",
    content: "HOLD on d1. COMPROMISE on d2: I write middleware.py if a2 exports verify_access_token.",
    addresses_issues: ["d1", "d2"],
    ts: "2026-09-12T03:00:48.000Z",
  },
  {
    type: "agent_message",
    room_id: ROOM,
    round: 1,
    agent_id: "a2",
    content: "HOLD on d1. COMPROMISE: both mechanisms; I will not touch middleware.py.",
    addresses_issues: ["d1", "d2"],
    ts: "2026-09-12T03:00:49.000Z",
  },
  {
    type: "user_message",
    room_id: ROOM,
    round: 1,
    user_id: "u1",
    content: "Do not concede the cookie path. Dual authenticator is fine.",
    ts: "2026-09-12T03:00:50.000Z",
  },
  { type: "round_complete", room_id: ROOM, round: 1, ts: "2026-09-12T03:01:00.000Z" },

  /* ---- round 2: converged ---- */
  {
    type: "analysis",
    room_id: ROOM,
    round: 2,
    similarities: [
      {
        topic: "Dual authenticator",
        detail: "Cookie first, then Bearer. Disjoint files.",
      },
    ],
    differences: [
      {
        issue_id: "d3",
        topic: "Login response shape",
        positions: { a1: "204 + Set-Cookie.", a2: "JSON token body." },
        severity: "minor",
      },
    ],
    converged: true,
    ts: "2026-09-12T03:01:40.000Z",
  },
  { type: "round_complete", room_id: ROOM, round: 2, ts: "2026-09-12T03:01:41.000Z" },

  /* ---- the plan, and the approval gate ---- */
  {
    type: "plan_proposed",
    room_id: ROOM,
    plan: {
      plan_id: "plan_demo01",
      rounds_used: 2,
      summary: "Both session cookies and bearer JWT behind one authenticate_user.",
      steps: [
        {
          step_id: "s1",
          title: "Session helpers",
          description: "HttpOnly cookie.",
          files_touched: ["src/auth/session.py"],
          rationale: "u1 must.",
        },
        {
          step_id: "s2",
          title: "JWT helpers",
          description: "HS256 15m.",
          files_touched: ["src/auth/jwt.py"],
          rationale: "u2 must.",
        },
      ],
      resolutions: [
        {
          issue_id: "d1",
          outcome: "Both mechanisms ship.",
          rationale: "Neither must was conceded.",
        },
        { issue_id: "d2", outcome: "a1 owns middleware.py.", rationale: "One writer per path." },
        {
          issue_id: "d3",
          outcome: "POST /login sets cookie and returns token JSON.",
          rationale: "Minor; satisfy both.",
        },
      ],
      approvals: { u1: false, u2: false },
      status: "proposed",
    },
    ts: "2026-09-12T03:02:00.000Z",
  },
  {
    type: "approval_updated",
    room_id: ROOM,
    user_id: "u1",
    approved: true,
    ts: "2026-09-12T03:02:10.000Z",
  },
  {
    type: "approval_updated",
    room_id: ROOM,
    user_id: "u2",
    approved: true,
    ts: "2026-09-12T03:02:18.000Z",
  },
  { type: "plan_approved", room_id: ROOM, plan_id: "plan_demo01", ts: "2026-09-12T03:02:20.000Z" },
  {
    type: "context_updated",
    room_id: ROOM,
    version: 2,
    content:
      "authenticate_user accepts a session cookie or a Bearer JWT. a1 owns session.py and middleware.py. a2 owns jwt.py, app.py, tests/test_auth.py.",
    ts: "2026-09-12T03:02:21.000Z",
  },

  /* ---- decomposition into tickets with disjoint file ownership ---- */
  {
    type: "tickets_created",
    room_id: ROOM,
    tickets: [
      {
        ticket_id: "t1",
        plan_id: "plan_demo01",
        title: "Session cookie helpers",
        description: "session.py",
        assigned_agent: "a1",
        files_owned: ["src/auth/session.py"],
        depends_on: [],
        lane: "parallel",
        status: "pending",
      },
      {
        ticket_id: "t2",
        plan_id: "plan_demo01",
        title: "JWT issue and verify",
        description: "jwt.py",
        assigned_agent: "a2",
        files_owned: ["src/auth/jwt.py"],
        depends_on: [],
        lane: "parallel",
        status: "pending",
      },
      {
        ticket_id: "t3",
        plan_id: "plan_demo01",
        title: "authenticate_user dispatcher",
        description: "middleware.py",
        assigned_agent: "a1",
        files_owned: ["src/auth/middleware.py"],
        depends_on: ["t1", "t2"],
        lane: "sequential",
        status: "pending",
      },
      {
        ticket_id: "t4",
        plan_id: "plan_demo01",
        title: "Login routes and tests",
        description: "app.py + tests",
        assigned_agent: "a2",
        files_owned: ["src/app.py", "tests/test_auth.py"],
        depends_on: ["t3"],
        lane: "sequential",
        status: "pending",
      },
    ],
    ts: "2026-09-12T03:02:25.000Z",
  },

  /* ---- execution: two agents write in parallel, one write is refused ---- */
  {
    type: "ticket_started",
    room_id: ROOM,
    ticket_id: "t1",
    agent_id: "a1",
    ts: "2026-09-12T03:03:00.000Z",
  },
  {
    type: "ticket_started",
    room_id: ROOM,
    ticket_id: "t2",
    agent_id: "a2",
    ts: "2026-09-12T03:03:00.100Z",
  },
  {
    type: "file_written",
    room_id: ROOM,
    ticket_id: "t1",
    agent_id: "a1",
    path: "src/auth/session.py",
    accepted: true,
    reason: null,
    ts: "2026-09-12T03:03:08.000Z",
  },
  // The moment the whole thing is built toward: a2 reaches for a file it does not
  // own, and the capability check refuses it.
  {
    type: "file_written",
    room_id: ROOM,
    ticket_id: "t2",
    agent_id: "a2",
    path: "src/auth/middleware.py",
    accepted: false,
    reason:
      "path_not_owned: src/auth/middleware.py belongs to ticket t3 (owner a1). Agent a2 owns: src/auth/jwt.py.",
    ts: "2026-09-12T03:03:12.000Z",
  },
  {
    type: "file_written",
    room_id: ROOM,
    ticket_id: "t2",
    agent_id: "a2",
    path: "src/auth/jwt.py",
    accepted: true,
    reason: null,
    ts: "2026-09-12T03:03:14.000Z",
  },
  {
    type: "ticket_completed",
    room_id: ROOM,
    ticket_id: "t1",
    status: "done",
    ts: "2026-09-12T03:03:20.000Z",
  },
  {
    type: "ticket_completed",
    room_id: ROOM,
    ticket_id: "t2",
    status: "done",
    ts: "2026-09-12T03:03:21.000Z",
  },
];

/**
 * The sixteenth event type. Kept out of the happy path so the demo stream stays
 * clean — dispatch it by hand to check the error rendering.
 */
export const errorEvent: ErrorEvent = {
  type: "error",
  room_id: ROOM,
  where: "k2.analysis.round_2",
  detail: "moderator returned malformed JSON; repaired on retry",
  ts: "2026-09-12T03:01:35.000Z",
};

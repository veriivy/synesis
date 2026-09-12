// Two checks, no test runner:
//   1. lib/fixtures.ts has not drifted from /fixtures/stream.jsonl.
//   2. Replaying that stream through roomReducer lands on the state we expect.
// Run with: npm run check:reducer

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { initialRoomState, replay, type RoomState } from "../lib/roomReducer.ts";
import { fixtureEvents } from "../lib/fixtures.ts";
import { workspaceFiles } from "../lib/workspace.ts";

/* ---- 1. fixtures.ts matches the frozen contract, field for field ---- */

const here = dirname(fileURLToPath(import.meta.url));
const jsonl = readFileSync(resolve(here, "../../fixtures/stream.jsonl"), "utf8")
  .split("\n")
  .map((l) => l.trim())
  .filter(Boolean)
  .map((l) => JSON.parse(l));

assert.equal(fixtureEvents.length, jsonl.length, "fixtures.ts and stream.jsonl differ in length");
fixtureEvents.forEach((event, i) => {
  assert.deepEqual(event, jsonl[i], `fixtures.ts event ${i} (${event.type}) drifted from stream.jsonl`);
});

/* ---- 2. the reducer ---- */

const seed: RoomState = {
  ...initialRoomState,
  files: Object.fromEntries(
    workspaceFiles.map((f) => [
      f.path,
      { content: f.content, lastWrittenBy: null, pendingContent: f.after },
    ]),
  ),
};

const state = replay(fixtureEvents, seed);

assert.equal(state.roomId, "room_demo01");
assert.equal(state.participants.length, 2, "two participants");
assert.deepEqual(
  state.participants.map((p) => [p.user_id, p.agent_id]),
  [
    ["u1", "a1"],
    ["u2", "a2"],
  ],
  "agent_id is learned from poa_generated",
);
assert.equal(state.currentRound, 2);
assert.equal(state.latestAnalysis?.round, 2);
assert.equal(state.latestAnalysis?.converged, true, "round 2 converged");
assert.equal(state.proposedPlan?.status, "approved", "plan approved after both approvals");
assert.deepEqual(state.approvals, { u1: true, u2: true });
assert.equal(state.tickets.length, 4);
assert.equal(state.tickets.filter((t) => t.status === "done").length, 2);
assert.equal(state.sharedContext?.version, 2);
assert.equal(state.status, "executing", "t3 and t4 have not run yet");

// Every event produced exactly one chat item, in arrival order.
assert.equal(state.messages.length, fixtureEvents.length, "one chat item per event");
assert.deepEqual(
  state.messages.slice(0, 6).map((m) => m.kind),
  ["participant", "participant", "poa", "poa", "analysis", "moderator"],
  "the conversation opens in contract order",
);

// The demo beat: a2 was refused middleware.py, and the file did not change.
assert.equal(state.writeLog.length, 3, "the log holds accepted and rejected writes");
const refused = state.writeLog.filter((w) => !w.accepted);
assert.equal(refused.length, 1);
assert.equal(refused[0].agent_id, "a2");
assert.equal(refused[0].path, "src/auth/middleware.py");
const middleware = state.files["src/auth/middleware.py"];
assert.equal(middleware.lastWrittenBy, null, "a refused write leaves no writer");
assert.ok(
  middleware.content.startsWith('"""authenticate_user dispatcher. Not written yet'),
  "a refused write does not change the file",
);

// Accepted writes did land, and swapped in the post-write content.
assert.equal(state.files["src/auth/session.py"].lastWrittenBy, "a1");
assert.ok(state.files["src/auth/session.py"].content.includes("def issue_session"));
assert.equal(state.files["src/auth/jwt.py"].lastWrittenBy, "a2");
assert.ok(state.files["src/auth/jwt.py"].content.includes("def verify_access_token"));

// The reducer is pure: replaying twice from the same seed gives the same result.
assert.deepEqual(replay(fixtureEvents, seed), state, "replay is deterministic");

console.log(
  `ok — ${fixtureEvents.length} events match stream.jsonl and replay to ${state.messages.length} chat items`,
);

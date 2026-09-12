// Replays /fixtures/stream.jsonl through roomReducer and asserts the end state.
// Run with: npm run check:reducer   (node strips the types; no test runner needed)

import assert from "node:assert/strict";
import { initialRoomState, replay, type RoomState } from "../lib/roomReducer.ts";
import { fixtureStream } from "../lib/fixtures/stream.ts";
import { workspaceFiles } from "../lib/fixtures/workspace.ts";

const seed: RoomState = {
  ...initialRoomState,
  files: Object.fromEntries(
    workspaceFiles.map((f) => [
      f.path,
      {
        path: f.path,
        content: f.content,
        pending_content: f.after,
        language: f.language,
        rejected_by: [],
      },
    ]),
  ),
};

const state = replay(fixtureStream, seed);

assert.equal(state.room_id, "room_demo01");
assert.equal(Object.keys(state.participants).length, 2, "two participants");
assert.equal(Object.keys(state.poas).length, 2, "two PoAs");
assert.deepEqual(state.agent_owner, { a1: "u1", a2: "u2" }, "agents map to their users");
assert.equal(state.analyses.length, 2, "two analysis rounds");
assert.equal(state.analyses[1].converged, true, "round 2 converged");
assert.equal(state.round, 2);
assert.equal(state.plan?.status, "approved", "plan approved after both approvals");
assert.deepEqual(state.approvals, { u1: true, u2: true });
assert.equal(state.tickets.length, 4);
assert.equal(state.tickets.filter((t) => t.status === "done").length, 2);
assert.equal(state.context?.version, 2);
assert.equal(state.phase, "executing", "t3 and t4 have not run yet");

// The demo beat: a2 was refused middleware.py, and the file did not change.
const middleware = state.files["src/auth/middleware.py"];
assert.equal(middleware.rejected_by.length, 1, "one refused write on middleware.py");
assert.equal(middleware.rejected_by[0].agent_id, "a2");
assert.equal(middleware.last_written_by, undefined, "refused write did not land");
assert.ok(middleware.content.startsWith('"""authenticate_user dispatcher. Not written yet'));

// Accepted writes did land, and swapped in the post-write content.
assert.equal(state.files["src/auth/session.py"].last_written_by, "a1");
assert.ok(state.files["src/auth/session.py"].content.includes("def issue_session"));
assert.equal(state.files["src/auth/jwt.py"].last_written_by, "a2");
assert.ok(state.files["src/auth/jwt.py"].content.includes("def verify_access_token"));

// Every event produced exactly one chat item, minus the u1/u2 approvals that the
// plan already recorded as false -> true (those do add rows) and no dropped events.
assert.equal(state.items.length, fixtureStream.length, "one chat item per event");

// The reducer is pure: replaying twice from the same seed gives the same result.
assert.deepEqual(replay(fixtureStream, seed).items, state.items, "replay is deterministic");

console.log(`ok — ${fixtureStream.length} events replayed, ${state.items.length} chat items`);

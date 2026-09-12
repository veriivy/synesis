/**
 * The demo's two conflicting intent sets, mirrored from `fixtures/intents.json`.
 *
 * Copied rather than imported so the frontend has no build-time dependency on a path
 * outside `/web`, and so nobody has to edit `/fixtures` — which is frozen — to tweak
 * demo copy. If you change the conflict, change both.
 */

import type { Requirement } from "./types";

export const DEMO_INTENTS: {
  feature: string;
  participants: {
    user_id: string;
    agent_id: string;
    display_name: string;
    requirements: Requirement[];
  }[];
} = {
  feature:
    "Add user authentication to the notes API so each user only sees their own notes.",
  participants: [
    {
      user_id: "u1",
      agent_id: "claude",
      display_name: "Audrey",
      requirements: [
        {
          req_id: "r1",
          text: "No external services. Everything runs on our own box, no Auth0, no Firebase, no hosted identity provider.",
          priority: "must-have",
        },
        {
          req_id: "r2",
          text: "A logged-out user must be logged out immediately. Revoking a session has to take effect on the very next request.",
          priority: "must-have",
        },
        {
          req_id: "r3",
          text: "Keep the existing pytest suite green. Do not rewrite the test harness.",
          priority: "nice-to-have",
        },
      ],
    },
    {
      user_id: "u2",
      agent_id: "gpt",
      display_name: "Sam",
      requirements: [
        {
          req_id: "r4",
          text: "Auth must be stateless. Any API node can verify a request without a shared session store, so we can scale horizontally later.",
          priority: "must-have",
        },
        {
          req_id: "r5",
          text: "The mobile client cannot use cookies. The token has to travel in an Authorization header.",
          priority: "must-have",
        },
        {
          req_id: "r6",
          text: "Auth check should add under 5ms per request.",
          priority: "nice-to-have",
        },
      ],
    },
  ],
};

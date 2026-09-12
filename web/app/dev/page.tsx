"use client";

import dynamic from "next/dynamic";

// DevRoom seeds its room state with a synthetic "Dev joined" event whose ts
// is new Date().toISOString() at the moment useRoom's lazy useState
// initializer runs — that happens once during SSR and again during client
// hydration, at two different real timestamps, which is a genuine (if
// cosmetic) hydration mismatch: React logs an error and regenerates the
// tree client-side. Found by an automated Playwright pass, confirmed via
// the exact server-vs-client timestamp diff in the console error.
//
// Every other page-rendered timestamp comes from a fixed fixture ts string
// (or, on a live room, a real backend event) — deterministic, no mismatch.
// This is specific to /dev's synthetic bootstrap join, so the fix is
// scoped to just this page: skip SSR for it entirely rather than touching
// useRoom.ts/useFixturePlayer.ts, which / also depends on and which don't
// have this problem (there, joining only ever happens from a client click,
// never during a server render).
const DevRoom = dynamic(() => import("./DevRoom"), { ssr: false });

export default function DevPage() {
  return <DevRoom />;
}

/** The five frozen endpoints, and nothing else. */

import type { FileEntry, Requirement, RoomSummary } from "./types";

/**
 * The backend's address. Must be absolute — we talk to the Python server directly
 * rather than proxying through Next, so that the SSE stream isn't buffered by a
 * rewrite. See the note in next.config.ts.
 *
 * NEXT_PUBLIC_ is not decoration: only variables with that prefix are readable in the
 * browser. Rename it and the value silently becomes undefined at runtime.
 */
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`${response.status} ${path}: ${detail.slice(0, 200)}`);
  }
  return response.json() as Promise<T>;
}

export const streamUrl = (roomId: string) => `${BASE}/rooms/${roomId}/stream`;

export const createRoom = (feature: string, repoUrl?: string) =>
  request<{ room_id: string }>("/rooms", {
    method: "POST",
    body: JSON.stringify({ feature, repo_url: repoUrl ?? "" }),
  });

export const getRoom = (roomId: string) => request<RoomSummary>(`/rooms/${roomId}`);

export const postIntent = (
  roomId: string,
  intent: {
    user_id: string;
    agent_id: string;
    requirements: Requirement[];
    display_name?: string;
  },
) =>
  request<{ ok: boolean }>(`/rooms/${roomId}/intents`, {
    method: "POST",
    body: JSON.stringify(intent),
  });

export const approvePlan = (roomId: string, approved: boolean, notes?: string) =>
  request<{ ok: boolean }>(`/rooms/${roomId}/plan/approve`, {
    method: "POST",
    body: JSON.stringify({ approved, notes: notes ?? null }),
  });

export const getFiles = (roomId: string) =>
  request<{ files: FileEntry[] }>(`/rooms/${roomId}/files`);

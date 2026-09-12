/** The five frozen endpoints, and nothing else. */

import type { FileEntry, Requirement, RoomSummary } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

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

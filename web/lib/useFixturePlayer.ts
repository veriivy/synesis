"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SSEEvent, SSEEventType } from "./types";
import { replay, roomReducer, type RoomState } from "./roomReducer";

/** The spec'd tick. Every event lands 1.2s after the one before it. */
export const DEFAULT_INTERVAL_MS = 1200;

export interface FixturePlayer {
  state: RoomState;
  /** The stream being replayed, for the inspector list. */
  events: SSEEvent[];
  /** How many events have been dispatched. */
  index: number;
  total: number;
  playing: boolean;
  speed: number;
  atEnd: boolean;
  /** The event most recently dispatched, for the inspector. */
  lastEvent: SSEEvent | null;
  /** The event that will go next, for the inspector. */
  nextEvent: SSEEvent | null;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  step: () => void;
  reset: () => void;
  /** Replay from the seed up to `i` events. Cheap: the reducer is pure. */
  seekTo: (i: number) => void;
  setSpeed: (s: number) => void;
  /** Dispatch an event that did not come off the stream (a local approval, a chat message). */
  dispatch: (event: SSEEvent) => void;
}

export interface FixturePlayerOptions {
  events: SSEEvent[];
  /** Builds the starting state. Called again on reset and on every seek. */
  seed: () => RoomState;
  intervalMs?: number;
  /** Per-event override of the tick, so a demo can breathe where it matters. */
  delayFor?: (event: SSEEvent) => number | undefined;
  /** Event types that stop playback when dispatched — the approval gate. */
  pauseAfter?: SSEEventType[];
  /** Side effects the room wants on each event, e.g. following a file write. */
  onEvent?: (event: SSEEvent) => void;
  autoPlay?: boolean;
}

/**
 * Drives a fixture array through roomReducer on a timer. This is the whole
 * development harness: no orchestrator, no network, deterministic every run.
 * Swapping in a live SSE stream means calling `dispatch` from an EventSource
 * and dropping the timer — the reducer and every component stay as they are.
 */
export function useFixturePlayer(options: FixturePlayerOptions): FixturePlayer {
  const { events, intervalMs = DEFAULT_INTERVAL_MS, autoPlay = false } = options;

  // Held in a ref so the ticking effect never has to re-subscribe on a new
  // closure. Synced in an effect that is declared before the timer effect, so
  // it is current by the time a tick is scheduled.
  const opts = useRef(options);
  useEffect(() => {
    opts.current = options;
  });

  const [state, setState] = useState<RoomState>(() => options.seed());
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(autoPlay);
  const [speed, setSpeed] = useState(1);

  const atEnd = index >= events.length;
  const lastEvent = index > 0 ? (events[index - 1] ?? null) : null;
  const nextEvent = events[index] ?? null;

  const dispatch = useCallback((event: SSEEvent) => {
    setState((prev) => roomReducer(prev, event));
    opts.current.onEvent?.(event);
  }, []);

  // The cursor lives in a ref as well as in state: dispatching is a side effect
  // and must never happen inside a state updater, which React is free to call
  // more than once.
  const cursor = useRef(0);

  const advance = useCallback(() => {
    const i = cursor.current;
    const event = opts.current.events[i];
    if (!event) return;
    cursor.current = i + 1;
    setIndex(i + 1);
    dispatch(event);
    if (opts.current.pauseAfter?.includes(event.type)) setPlaying(false);
  }, [dispatch]);

  useEffect(() => {
    const event = events[index];
    if (!playing || !event) return;
    const delay = (opts.current.delayFor?.(event) ?? intervalMs) / speed;
    const timer = setTimeout(advance, delay);
    return () => clearTimeout(timer);
  }, [playing, index, speed, events, intervalMs, advance]);

  const reset = useCallback(() => {
    setPlaying(false);
    cursor.current = 0;
    setIndex(0);
    setState(opts.current.seed());
  }, []);

  const seekTo = useCallback((i: number) => {
    const target = Math.max(0, Math.min(i, opts.current.events.length));
    setPlaying(false);
    cursor.current = target;
    setIndex(target);
    setState(replay(opts.current.events.slice(0, target), opts.current.seed()));
  }, []);

  return {
    state,
    events,
    index,
    total: events.length,
    playing: playing && !atEnd,
    speed,
    atEnd,
    lastEvent,
    nextEvent,
    play: () => setPlaying(true),
    pause: () => setPlaying(false),
    toggle: () => setPlaying((p) => !p),
    step: () => {
      setPlaying(false);
      advance();
    },
    reset,
    seekTo,
    setSpeed,
    dispatch,
  };
}

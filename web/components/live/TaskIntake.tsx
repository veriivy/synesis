"use client";

import { useState } from "react";
import type { Priority } from "@/lib/types";

/**
 * The live room's stand-in for the seeded task lists fixtures/tasks.json
 * provides for free. Collects the local user's own requirements before
 * POST /negotiate fires — the peer's side is fixed (see DEMO_TASK_U2 in
 * useLiveRoom.ts), since this MVP has one real browser per room.
 */
export function TaskIntake({
  onSubmit,
}: {
  onSubmit: (tasks: { text: string; priority: Priority }[]) => void;
}) {
  const [text, setText] = useState(
    "Handle authentication using session cookies for the browser.",
  );
  const [priority, setPriority] = useState<Priority>("must");
  const [submitting, setSubmitting] = useState(false);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || submitting) return;
    setSubmitting(true);
    onSubmit([{ text: text.trim(), priority }]);
  }

  return (
    <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/40">
      <form
        onSubmit={submit}
        className="w-[420px] rounded-md border border-ide-border bg-ide-panel p-4 font-mono text-[12px] text-ide-text"
      >
        <div className="mb-3 text-[13px] font-semibold">Your requirement</div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          className="mb-3 w-full resize-none rounded-sm border border-ide-border bg-ide-bg p-2 text-[12px] text-ide-text outline-none"
          placeholder="What does your agent need to build?"
        />
        <div className="mb-4 flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-ide-dim">
            <input
              type="radio"
              checked={priority === "must"}
              onChange={() => setPriority("must")}
            />
            must
          </label>
          <label className="flex items-center gap-1.5 text-ide-dim">
            <input
              type="radio"
              checked={priority === "want"}
              onChange={() => setPriority("want")}
            />
            want
          </label>
        </div>
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-sm border border-ide-border bg-ide-hover px-3 py-2 text-[12px] text-ide-text disabled:opacity-50"
        >
          {submitting ? "Starting negotiation…" : "Submit & start negotiation"}
        </button>
      </form>
    </div>
  );
}

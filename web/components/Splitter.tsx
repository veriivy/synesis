"use client";

import { useRef } from "react";

function clamp(n: number, min: number, max: number) {
  return Math.min(Math.max(n, min), max);
}

/**
 * A draggable divider. The parent owns the size in px; this only reports deltas.
 * Keyboard-operable, because a 4px target is not a control everyone can hit.
 */
export function Splitter({
  axis,
  value,
  onChange,
  min = 120,
  max = () => Number.POSITIVE_INFINITY,
  /** true when growing means dragging toward the start of the axis (a right-hand rail). */
  inverted = false,
  label,
}: {
  axis: "x" | "y";
  value: number;
  onChange: (next: number) => void;
  min?: number;
  max?: () => number;
  inverted?: boolean;
  label: string;
}) {
  const drag = useRef<{ from: number; value: number } | null>(null);

  function position(e: React.PointerEvent) {
    return axis === "x" ? e.clientX : e.clientY;
  }

  function apply(next: number) {
    onChange(clamp(next, min, Math.max(min, max())));
  }

  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      aria-valuenow={Math.round(value)}
      tabIndex={0}
      onPointerDown={(e) => {
        drag.current = { from: position(e), value };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        const delta = position(e) - drag.current.from;
        apply(drag.current.value + (inverted ? -delta : delta));
      }}
      onPointerUp={(e) => {
        drag.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
      }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 48 : 16;
        const back = axis === "x" ? "ArrowLeft" : "ArrowUp";
        const forward = axis === "x" ? "ArrowRight" : "ArrowDown";
        if (e.key !== back && e.key !== forward) return;
        e.preventDefault();
        const delta = e.key === forward ? step : -step;
        apply(value + (inverted ? -delta : delta));
      }}
      className={
        axis === "x"
          ? "group relative z-10 w-1 shrink-0 cursor-col-resize bg-ide-border hover:bg-ide-accent"
          : "group relative z-10 h-1 shrink-0 cursor-row-resize bg-ide-border hover:bg-ide-accent"
      }
      style={{ touchAction: "none" }}
      title={`${label} — drag, or focus and use the arrow keys`}
    >
      {/* A wider invisible hit area than the 4px line the eye sees. */}
      <span
        aria-hidden
        className={
          axis === "x"
            ? "absolute inset-y-0 -left-1 -right-1 block"
            : "absolute inset-x-0 -top-1 -bottom-1 block"
        }
      />
    </div>
  );
}

import type { ReactNode } from "react";
import type { Provider } from "@/lib/contract";

export const PROVIDER_COLOR: Record<Provider, string> = {
  claude: "#d97757",
  gpt: "#10a37f",
  gemini: "#4285f4",
};

export const PROVIDER_LABEL: Record<Provider, string> = {
  claude: "Claude",
  gpt: "GPT",
  gemini: "Gemini",
};

export function ProviderDot({ provider, size = 7 }: { provider?: Provider; size?: number }) {
  return (
    <span
      className="inline-block shrink-0 rounded-full"
      style={{
        width: size,
        height: size,
        background: provider ? PROVIDER_COLOR[provider] : "var(--color-ide-faint)",
      }}
    />
  );
}

export function Tag({
  children,
  color,
  title,
  upper = false,
}: {
  children: ReactNode;
  color?: string;
  title?: string;
  /** Identifiers (d1, t3) keep their case; status words are set in caps. */
  upper?: boolean;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-sm border px-1.5 py-px font-mono text-[10px] leading-4 tracking-wide ${
        upper ? "uppercase" : ""
      }`}
      style={{
        borderColor: color ? `${color}55` : "var(--color-ide-border)",
        color: color ?? "var(--color-ide-dim)",
        background: color ? `${color}14` : "transparent",
      }}
    >
      {children}
    </span>
  );
}

export function Label({ children }: { children: ReactNode }) {
  return (
    <div className="mb-1.5 font-mono text-[10px] tracking-wider text-ide-faint uppercase">
      {children}
    </div>
  );
}

export function Card({
  children,
  accent,
  className = "",
}: {
  children: ReactNode;
  accent?: string;
  className?: string;
}) {
  return (
    <div
      className={`rounded border bg-ide-panel ${className}`}
      style={{ borderColor: accent ? `${accent}55` : "var(--color-ide-border)" }}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  children,
  accent,
}: {
  children: ReactNode;
  accent?: string;
}) {
  return (
    <div
      className="flex items-center gap-2 border-b px-3 py-2"
      style={{ borderColor: accent ? `${accent}33` : "var(--color-ide-border)" }}
    >
      {children}
    </div>
  );
}

export function clock(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

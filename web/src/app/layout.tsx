import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Synesis — agents that argue before they code",
  description:
    "Each collaborator brings their own AI agent. The agents negotiate the plan, surface every conflict, and only write code into files they own.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // `dark` is fixed rather than system-derived: this gets projected in a bright room
  // onto a screen behind a presenter, and the palette is built for that.
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}

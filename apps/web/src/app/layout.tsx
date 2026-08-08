import type { Metadata } from "next";

import { AuthGate } from "@/components/auth/AuthGate";
import { AppShell } from "@/components/shell/AppShell";

import "./globals.css";

export const metadata: Metadata = {
  title: "Drake",
  description: "Observability and operations control plane",
};

/**
 * Applies the persisted (or system) theme before hydration to avoid a flash
 * of the wrong theme. localStorage only ever stores "light" or "dark".
 */
const themeInitScript = `
(function () {
  try {
    var stored = localStorage.getItem("drake-theme");
    var dark = stored ? stored === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    if (dark) document.documentElement.classList.add("dark");
  } catch (_e) {}
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <AuthGate>
          <AppShell>{children}</AppShell>
        </AuthGate>
      </body>
    </html>
  );
}

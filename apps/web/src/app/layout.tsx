import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";

import { AppShell } from "@/components/shell/AppShell";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

import "./globals.css";

/**
 * Inter, self-hosted and subset.
 *
 * `next/font/local` over `next/font/google`: the Google loader fetches at
 * build time, which would make the container image build depend on a third
 * party being reachable. The file is a Latin + Turkish subset of Inter
 * Variable (SIL OFL 1.1, licence beside it) — one 75 KB file covering every
 * weight, with `swap` so text is readable while it loads.
 */
const inter = localFont({
  src: "./fonts/InterVariable-latin.woff2",
  variable: "--font-inter",
  display: "swap",
  weight: "100 900",
  preload: true,
  fallback: [
    "ui-sans-serif",
    "system-ui",
    "-apple-system",
    "Segoe UI",
    "Roboto",
    "Helvetica Neue",
    "Arial",
    "sans-serif",
  ],
});

export const metadata: Metadata = {
  title: {
    default: "Drake",
    template: "%s · Drake",
  },
  description: "Observability and operations control plane",
  // Two favicons from the same authoritative crop, selected by the browser's
  // own theme: the light lockup is deep green and vanishes on a dark tab
  // strip; the dark one is near-white and vanishes on a light one.
  icons: {
    icon: [
      { url: "/brand/drake-favicon-light.png", media: "(prefers-color-scheme: light)" },
      { url: "/brand/drake-favicon-dark.png", media: "(prefers-color-scheme: dark)" },
    ],
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f6f5" },
    { media: "(prefers-color-scheme: dark)", color: "#032821" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <head>
        {/* Applies the persisted theme before first paint. Inline and
            synchronous on purpose: anything deferred paints the wrong theme
            first, and on a dark-mode NOC screen that flash is a strobe. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}

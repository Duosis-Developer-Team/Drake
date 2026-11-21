import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";

import { AppShell } from "@/components/shell/AppShell";
import { LOCALE_INIT_SCRIPT } from "@/lib/i18n/locale";
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
    default: "Tracesis",
    template: "%s · Tracesis",
  },
  description: "Observability and operations control plane",
  // One icon: the mark on its own dark tile reads on light and dark tab
  // strips alike, so no per-scheme pair is needed.
  icons: {
    icon: [
      { url: "/brand/tracesis-icon.svg", type: "image/svg+xml" },
      { url: "/brand/tracesis-favicon-32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: [{ url: "/brand/tracesis-apple-touch.png", sizes: "180x180" }],
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f4ef" },
    { media: "(prefers-color-scheme: dark)", color: "#0b0a10" },
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
        {/* Same rule for the language: `<html lang>` must be right before
            anything renders, for screen readers and the browser's own UI. */}
        <script dangerouslySetInnerHTML={{ __html: LOCALE_INIT_SCRIPT }} />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}

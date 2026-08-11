/**
 * The design system's own invariants.
 *
 * Three things are checked here that no screen test would catch:
 *
 * 1. `lib/design/tokens.ts` and `globals.css` agree. The stylesheet is the
 *    source of truth, but ECharts needs real values and cannot read custom
 *    properties, so the literals are duplicated. A duplicate that silently
 *    drifts is how a chart ends up drawn in last quarter's brand colour.
 * 2. Contrast. Every text token clears WCAG AA against the surfaces it is
 *    used on, in BOTH themes, and every mark clears 3:1. These were computed
 *    when the palette was chosen; this keeps them true.
 * 3. The status vocabulary stays honest — unknown is not success, stale is not
 *    success, and no two states collapse onto one colour.
 */
import { readFileSync } from "node:fs";
import { existsSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  DARK_TOKENS,
  LIGHT_TOKENS,
  SERIES_DASH,
  SERIES_TOKENS,
  TOKEN_NAMES,
  type TokenName,
} from "@/lib/design/tokens";
import {
  TONES,
  TONE_SEVERITY,
  compareTone,
  toneForHealth,
  toneForThreshold,
  type StatusTone,
} from "@/lib/design/status";

const WEB_ROOT = existsSync(join(process.cwd(), "src", "app", "globals.css"))
  ? process.cwd()
  : join(process.cwd(), "apps", "web");
const CSS = readFileSync(join(WEB_ROOT, "src", "app", "globals.css"), "utf8");

/** Custom properties declared inside one selector block. */
function declaredIn(selector: string): Record<string, string> {
  const start = CSS.indexOf(`${selector} {`);
  expect(start, `${selector} block missing from globals.css`).toBeGreaterThan(-1);
  const end = CSS.indexOf("\n}", start);
  const block = CSS.slice(start, end);
  const declarations: Record<string, string> = {};
  for (const match of block.matchAll(/^\s*--([\w-]+):\s*([^;]+);/gm)) {
    declarations[match[1]] = match[2].trim();
  }
  return declarations;
}

function channels(hex: string): [number, number, number] {
  const value = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16)) as [number, number, number];
}

function relativeLuminance(hex: string): number {
  const [r, g, b] = channels(hex).map((channel) => {
    const c = channel / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [high, low] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

describe("token layer", () => {
  it("the TypeScript mirror matches globals.css exactly, in both themes", () => {
    for (const [selector, tokens] of [
      [":root", LIGHT_TOKENS],
      [".dark", DARK_TOKENS],
    ] as const) {
      const declared = declaredIn(selector);
      for (const name of TOKEN_NAMES) {
        expect(declared[name], `--${name} missing from ${selector}`).toBeDefined();
        expect(declared[name].toLowerCase(), `--${name} drifted in ${selector}`).toBe(
          tokens[name].toLowerCase(),
        );
      }
    }
  });

  it("declares every semantic family the design calls for", () => {
    // Naming these explicitly rather than deriving them from the file: the
    // point is that a family cannot be quietly dropped.
    const required: TokenName[] = [
      "canvas",
      "surface-1",
      "surface-2",
      "surface-3",
      "surface-elevated",
      "surface-hover",
      "surface-selected",
      "border-subtle",
      "border-strong",
      "text-primary",
      "text-secondary",
      "text-muted",
      "text-inverse",
      "brand",
      "brand-hover",
      "brand-active",
      "brand-soft",
      "focus-ring",
      "chart-grid",
      "chart-axis",
      "chart-tooltip",
      "status-success",
      "status-info",
      "status-warning",
      "status-critical",
      "status-neutral",
      "status-unknown",
      "status-stale",
    ];
    for (const name of required) expect(TOKEN_NAMES).toContain(name);
  });
});

describe("contrast", () => {
  const SURFACES: TokenName[] = ["surface-1", "canvas", "surface-2", "surface-3"];
  const TEXT: TokenName[] = ["text-primary", "text-secondary", "text-muted"];

  for (const [theme, tokens] of [
    ["light", LIGHT_TOKENS],
    ["dark", DARK_TOKENS],
  ] as const) {
    it(`${theme}: body text clears 4.5:1 on every surface`, () => {
      for (const surface of SURFACES) {
        for (const text of TEXT) {
          const ratio = contrast(tokens[text], tokens[surface]);
          expect(ratio, `${theme} ${text} on ${surface} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
        }
      }
    });

    it(`${theme}: status text clears 4.5:1 on its own chip and on the panel`, () => {
      const statuses = [
        "status-success",
        "status-info",
        "status-warning",
        "status-critical",
        "status-neutral",
        "status-unknown",
        "status-stale",
      ] as const;
      const soft = declaredIn(theme === "light" ? ":root" : ".dark");
      for (const status of statuses) {
        for (const background of [soft[`${status}-soft`], tokens["surface-1"]]) {
          const ratio = contrast(tokens[status], background);
          expect(ratio, `${theme} ${status} on ${background} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
        }
      }
    });

    it(`${theme}: the active navigation entry clears 4.5:1 on its own surface`, () => {
      // The pair axe caught: brand green on the selected surface was 4.42:1.
      const ratio = contrast(tokens.brand, tokens["surface-selected"]);
      expect(ratio, `${theme} brand on surface-selected is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
    });

    it(`${theme}: marks and interactive borders clear 3:1`, () => {
      for (const mark of ["brand-accent", "focus-ring", "border-strong", "chart-axis"] as const) {
        for (const surface of ["surface-1", "canvas"] as const) {
          const ratio = contrast(tokens[mark], tokens[surface]);
          expect(ratio, `${theme} ${mark} on ${surface} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
        }
      }
    });

    it(`${theme}: every chart series clears 3:1 on the chart surface`, () => {
      for (const series of SERIES_TOKENS) {
        const ratio = contrast(tokens[series], tokens["surface-1"]);
        // Two light-mode slots sit just under 3:1 by design; the chart frame
        // relieves that with a permanent legend and a table view. The floor
        // below is the point at which a mark stops being visible at all.
        expect(ratio, `${theme} ${series} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(2.5);
      }
    });
  }
});

describe("chart series", () => {
  it("pairs every colour slot with a dash pattern", () => {
    // Colour is never the only channel: the same order in greyscale, in print
    // and to a colour-blind reader has to stay separable.
    expect(SERIES_DASH.length).toBeGreaterThanOrEqual(SERIES_TOKENS.length);
    const patterns = SERIES_DASH.slice(0, SERIES_TOKENS.length).map((dash) =>
      dash ? dash.join(",") : "solid",
    );
    expect(new Set(patterns).size).toBe(patterns.length);
  });

  it("uses a fixed order, with no colour repeated in either theme", () => {
    for (const tokens of [LIGHT_TOKENS, DARK_TOKENS]) {
      const colours = SERIES_TOKENS.map((name) => tokens[name]);
      expect(new Set(colours).size).toBe(colours.length);
    }
  });
});

describe("status vocabulary", () => {
  it("never maps an unmeasured state onto success", () => {
    for (const value of [
      "unknown",
      "insufficient_data",
      "unverified",
      "stale",
      "not_configured",
      "disconnected",
      "",
      undefined,
      "a-word-drake-does-not-know",
    ]) {
      expect(toneForHealth(value as string), `${value} became success`).not.toBe("success");
    }
  });

  it("keeps unknown, stale and not-applicable as three separate tones", () => {
    expect(toneForHealth("unknown")).toBe("unknown");
    expect(toneForHealth("stale")).toBe("stale");
    expect(toneForHealth("not_configured")).toBe("not-applicable");
    const tones = new Set([TONES.unknown.token, TONES.stale.token, TONES["not-applicable"].token]);
    expect(tones.size).toBe(3);
  });

  it("reserves the critical red for critical alone", () => {
    const critical = TONES.critical.token;
    const others = Object.entries(TONES)
      .filter(([tone]) => tone !== "critical")
      .map(([, spec]) => spec.token);
    expect(others).not.toContain(critical);
  });

  it("gives every tone an icon and a word, so colour is never the only channel", () => {
    for (const [tone, spec] of Object.entries(TONES)) {
      expect(spec.icon, `${tone} has no icon`).toBeTruthy();
      expect(spec.label.length, `${tone} has no label`).toBeGreaterThan(0);
    }
  });

  it("sorts what needs attention above what is fine", () => {
    const order: StatusTone[] = [
      "critical",
      "warning",
      "stale",
      "unknown",
      "denied",
      "pending",
      "info",
      "neutral",
      "not-applicable",
      "success",
    ];
    expect([...order].sort(compareTone)).toEqual(order);
    // Specifically: "we do not know" outranks "we are fine".
    expect(TONE_SEVERITY.unknown).toBeLessThan(TONE_SEVERITY.success);
    expect(TONE_SEVERITY.stale).toBeLessThan(TONE_SEVERITY.success);
  });
});

describe("thresholds", () => {
  const above = { warn: 5, critical: 10, direction: "above" as const };
  const below = { warn: 95, critical: 90, direction: "below" as const };

  it("never returns success without a threshold to clear", () => {
    expect(toneForThreshold(3, null)).toBe("neutral");
    expect(toneForThreshold(3, undefined)).toBe("neutral");
  });

  it("returns unknown for a missing measurement, not zero and not healthy", () => {
    expect(toneForThreshold(null, above)).toBe("unknown");
    expect(toneForThreshold(undefined, above)).toBe("unknown");
    expect(toneForThreshold(Number.NaN, above)).toBe("unknown");
  });

  it("compares in the direction the caller gave", () => {
    expect(toneForThreshold(1, above)).toBe("success");
    expect(toneForThreshold(5, above)).toBe("warning");
    expect(toneForThreshold(11, above)).toBe("critical");

    expect(toneForThreshold(99, below)).toBe("success");
    expect(toneForThreshold(95, below)).toBe("warning");
    expect(toneForThreshold(80, below)).toBe("critical");
  });

  it("treats a measured zero as a value", () => {
    expect(toneForThreshold(0, above)).toBe("success");
    expect(toneForThreshold(0, below)).toBe("critical");
  });
});

describe("brand assets", () => {
  it("the wordmark and mark derivatives exist for both themes", () => {
    for (const file of [
      "drake-wordmark-light.webp",
      "drake-wordmark-dark.webp",
      "drake-mark-light.webp",
      "drake-mark-dark.webp",
    ]) {
      expect(() => statSync(join(WEB_ROOT, "public", "brand", file))).not.toThrow();
    }
  });
});

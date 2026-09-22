import { render, screen, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { formatMessage } from "@/lib/i18n/format";
import { LocaleProvider, formattersFor, getTranslator, useLocale, useT } from "@/lib/i18n";
import { LOCALE_STORAGE_KEY, writeLocale } from "@/lib/i18n/locale";
import { MESSAGES } from "@/lib/i18n/messages";

describe("formatMessage", () => {
  it("interpolates and leaves unknown variables empty", () => {
    expect(formatMessage("Hello {name}", { name: "Ada" })).toBe("Hello Ada");
    expect(formatMessage("Hello {name}", {})).toBe("Hello ");
  });

  it("selects English plural categories", () => {
    const template = "{count, plural, =0 {none} one {# incident} other {# incidents}}";
    expect(formatMessage(template, { count: 0 }, "en")).toBe("none");
    expect(formatMessage(template, { count: 1 }, "en")).toBe("1 incident");
    expect(formatMessage(template, { count: 7 }, "en")).toBe("7 incidents");
  });

  it("Turkish needs only `other`", () => {
    expect(formatMessage("{count, plural, other {# olay}}", { count: 1 }, "tr")).toBe("1 olay");
    expect(formatMessage("{count, plural, other {# olay}}", { count: 5 }, "tr")).toBe("5 olay");
  });

  it("selects by value with a fallback", () => {
    const template = "{state, select, open {Open} other {Closed}}";
    expect(formatMessage(template, { state: "open" })).toBe("Open");
    expect(formatMessage(template, { state: "weird" })).toBe("Closed");
  });

  it("nests arguments inside branches", () => {
    const template = "{count, plural, one {# item for {who}} other {# items for {who}}}";
    expect(formatMessage(template, { count: 2, who: "ops" })).toBe("2 items for ops");
  });
});

describe("catalogue completeness", () => {
  it("every namespace's Turkish tree has the same keys as its English tree", () => {
    const keysOf = (tree: object, prefix = ""): string[] =>
      Object.entries(tree).flatMap(([k, v]) =>
        typeof v === "string" ? [`${prefix}${k}`] : keysOf(v as object, `${prefix}${k}.`),
      );
    for (const [name, ns] of Object.entries(MESSAGES)) {
      const en = keysOf(ns.en).sort();
      const tr = keysOf(ns.tr).sort();
      expect({ namespace: name, keys: tr }).toEqual({ namespace: name, keys: en });
    }
  });

  it("no Turkish leaf is left identical to English unless it is a proper noun or symbol", () => {
    // Proper nouns, units and symbols are legitimately shared. Anything else
    // identical in both trees is almost certainly an untranslated string.
    const allowed = /^(UTC|USD|%|SLO|SLI|GitHub|Helm|Drake|English|Türkçe|Namespace|Pod|OK|ID|—|-|\d[\d.,: ]*|[A-Z]{2,5}|\{[a-z]+\}(\s?ms)?)$/;
    const violations: string[] = [];
    const walk = (en: object, tr: object, prefix: string) => {
      for (const [k, v] of Object.entries(en)) {
        const other = (tr as Record<string, unknown>)[k];
        if (typeof v === "string") {
          if (v === other && !allowed.test(v)) violations.push(`${prefix}${k} = ${JSON.stringify(v)}`);
        } else walk(v as object, other as object, `${prefix}${k}.`);
      }
    };
    for (const [name, ns] of Object.entries(MESSAGES)) walk(ns.en, ns.tr, `${name}.`);
    expect(violations).toEqual([]);
  });
});

function Probe() {
  const t = useT("common");
  const { locale } = useLocale();
  return (
    <p>
      {locale}:{t("action.retry")}:{t("count.items", { count: 3 })}
    </p>
  );
}

describe("LocaleProvider", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    localStorage.clear();
    document.documentElement.lang = "en";
  });

  it("renders English without any provider", () => {
    render(<Probe />);
    expect(screen.getByText("en:Retry:3 items")).toBeInTheDocument();
  });

  it("pins a locale when told to", () => {
    render(
      <LocaleProvider locale="tr">
        <Probe />
      </LocaleProvider>,
    );
    expect(screen.getByText("tr:Yeniden dene:3 öğe")).toBeInTheDocument();
  });

  it("follows the stored preference and switches live", () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, "tr");
    render(
      <LocaleProvider>
        <Probe />
      </LocaleProvider>,
    );
    expect(screen.getByText("tr:Yeniden dene:3 öğe")).toBeInTheDocument();
    act(() => writeLocale("en"));
    expect(screen.getByText("en:Retry:3 items")).toBeInTheDocument();
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBeNull();
    expect(document.documentElement.lang).toBe("en");
  });
});

describe("formatters", () => {
  const now = new Date("2026-09-22T12:00:00Z");

  it("relative time reads naturally in both languages", () => {
    expect(formattersFor("en").relative("2026-09-22T11:56:00Z", now)).toBe("4m ago");
    expect(formattersFor("tr").relative("2026-09-22T11:56:00Z", now)).toBe("4 dk önce");
    expect(formattersFor("tr").relative("2026-09-22T14:00:00Z", now)).toBe("2 sa sonra");
    expect(formattersFor("tr").relative("2026-09-22T12:00:10Z", now)).toBe("az önce");
  });

  it("durations carry localized unit suffixes", () => {
    expect(formattersFor("en").duration(11520)).toBe("3h 12m");
    expect(formattersFor("tr").duration(11520)).toBe("3 sa 12 dk");
    expect(formattersFor("tr").duration(11520, { compact: true })).toBe("3 sa");
    expect(formattersFor("tr").duration(0.25)).toBe("250 ms");
  });

  it("timestamps stay UTC in every locale", () => {
    expect(formattersFor("tr").utc("2026-09-22T11:56:00Z")).toBe("2026-09-22 11:56:00 UTC");
  });

  it("getTranslator falls back to English for a key Turkish lacks at runtime", () => {
    const t = getTranslator("common", "tr");
    expect(t.dyn("health", "healthy")).toBe("Sağlıklı");
    expect(t.dyn("health", "no-such-token", "fallback")).toBe("fallback");
    expect(t.dyn("health", "no-such-token")).toBe("no-such-token");
  });
});

/**
 * The message formatter: a deliberately small subset of ICU MessageFormat.
 *
 * Supported:
 *   - `{name}`                                   simple interpolation
 *   - `{count, plural, one {# item} other {# items}}`
 *     `=0 {none}` exact matches, `#` is the number; categories come from
 *     `Intl.PluralRules` for the locale, so Turkish (which has only `other`
 *     for cardinals) works with just `other {...}` and English needs
 *     `one`/`other`.
 *   - `{kind, select, open {Open} other {Closed}}`
 *
 * Nothing else. Dates and numbers are formatted by the caller through
 * `useFormat()` and passed in as strings: a catalogue that formats dates is
 * one that silently disagrees with the rest of the product about UTC.
 */
import type { Locale } from "./locale";

export type MessageVars = Record<string, string | number | null | undefined>;

const pluralRules: Partial<Record<Locale, Intl.PluralRules>> = {};

function rulesFor(locale: Locale): Intl.PluralRules {
  return (pluralRules[locale] ??= new Intl.PluralRules(locale));
}

/** Split `a {b {c}} d` at top-level commas/spaces: the argument body parser. */
function parseBranches(body: string): Record<string, string> {
  const branches: Record<string, string> = {};
  let i = 0;
  while (i < body.length) {
    while (i < body.length && body[i] === " ") i++;
    let name = "";
    while (i < body.length && body[i] !== "{" && body[i] !== " ") name += body[i++];
    while (i < body.length && body[i] === " ") i++;
    if (body[i] !== "{") break;
    let depth = 0;
    let start = i;
    for (; i < body.length; i++) {
      if (body[i] === "{") {
        if (depth === 0) start = i + 1;
        depth++;
      } else if (body[i] === "}") {
        depth--;
        if (depth === 0) break;
      }
    }
    branches[name] = body.slice(start, i);
    i++;
  }
  return branches;
}

function formatArgument(argument: string, vars: MessageVars, locale: Locale): string {
  const first = argument.indexOf(",");
  if (first === -1) {
    const value = vars[argument.trim()];
    return value === null || value === undefined ? "" : String(value);
  }
  const name = argument.slice(0, first).trim();
  const rest = argument.slice(first + 1);
  const second = rest.indexOf(",");
  const kind = (second === -1 ? rest : rest.slice(0, second)).trim();
  const body = second === -1 ? "" : rest.slice(second + 1);
  const branches = parseBranches(body);
  const value = vars[name];

  if (kind === "plural") {
    const count = typeof value === "number" ? value : Number(value ?? 0);
    const branch =
      branches[`=${count}`] ?? branches[rulesFor(locale).select(count)] ?? branches.other ?? "";
    return formatMessage(branch.replace(/#/g, String(count)), vars, locale);
  }
  if (kind === "select") {
    const key = value === null || value === undefined ? "other" : String(value);
    return formatMessage(branches[key] ?? branches.other ?? "", vars, locale);
  }
  return "";
}

export function formatMessage(template: string, vars: MessageVars = {}, locale: Locale = "en"): string {
  let out = "";
  let i = 0;
  while (i < template.length) {
    const ch = template[i];
    if (ch === "'" && template[i + 1] === "{") {
      // `'{'` escapes a literal brace.
      out += "{";
      i += 2;
      continue;
    }
    if (ch !== "{") {
      out += ch;
      i++;
      continue;
    }
    let depth = 0;
    let j = i;
    for (; j < template.length; j++) {
      if (template[j] === "{") depth++;
      else if (template[j] === "}") {
        depth--;
        if (depth === 0) break;
      }
    }
    if (j >= template.length) {
      out += template.slice(i);
      break;
    }
    out += formatArgument(template.slice(i + 1, j), vars, locale);
    i = j + 1;
  }
  return out;
}

#!/usr/bin/env node
/**
 * Lists user-visible string literals that are still hardcoded in the web
 * source: JSX text, JSX string attributes people read (aria-label, title,
 * placeholder, alt, label, ...), and object-literal properties with those
 * names. A TypeScript AST scan, not a regex.
 *
 *   node scripts/i18n-scan.mjs                 # everything under src
 *   node scripts/i18n-scan.mjs src/app/alerts  # one area
 *   node scripts/i18n-scan.mjs --count         # totals per file only
 *
 * It over-reports on purpose (a CSS class in a `title` prop, a units string)
 * and it is a checklist, not a gate: a hit is something to look at, and
 * `// i18n-ignore` on the same line silences one.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";

const args = process.argv.slice(2);
const countOnly = args.includes("--count");
const roots = args.filter((a) => !a.startsWith("--"));
if (roots.length === 0) roots.push("src");

const ATTRS = new Set([
  "aria-label", "aria-description", "aria-placeholder", "aria-roledescription", "aria-valuetext",
  "title", "placeholder", "alt", "label", "description", "caption", "heading", "subtitle",
  "emptyTitle", "emptyBody", "errorTitle", "hint", "helpText", "tooltip", "name", "summary", "message",
  "loadingLabel", "action", "actionLabel", "cta", "text", "body", "detail",
]);
const SKIP_FILE = /(\.test\.tsx?$|\.d\.ts$|\/i18n\/|\/src\/test\/|\/design\/tokens\.ts$|\/api\.ts$)/;
const LETTERS = /[A-Za-z]{2,}/;
// Things that are not copy even though they contain letters.
const NOT_COPY = /^(https?:|\/|#|\d|[a-z0-9_-]+$|[a-z]+[A-Z][A-Za-z]*$|[A-Z_]{2,}$|[a-z]+(-[a-z0-9]+)+$|[A-Za-z]+\/[A-Za-z]|\{|%|sha256:|[a-z]+\.[a-z]+$)/;

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(entry) && !SKIP_FILE.test(full)) out.push(full);
  }
  return out;
}

function isCopy(text) {
  const trimmed = text.replace(/\s+/g, " ").trim();
  if (!LETTERS.test(trimmed)) return false;
  if (NOT_COPY.test(trimmed)) return false;
  return true;
}

const results = new Map();
for (const root of roots) {
  const files = statSync(root).isDirectory() ? walk(root) : [root];
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    const lines = text.split("\n");
    const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const hits = [];
    const add = (node, value) => {
      const { line } = source.getLineAndCharacterOfPosition(node.getStart());
      if (/i18n-ignore/.test(lines[line])) return;
      hits.push({ line: line + 1, value: value.replace(/\s+/g, " ").trim() });
    };
    const visit = (node) => {
      if (ts.isJsxText(node) && isCopy(node.text)) add(node, node.text);
      else if (ts.isJsxAttribute(node) && node.initializer && ATTRS.has(node.name.getText())) {
        const init = node.initializer;
        if (ts.isStringLiteral(init) && isCopy(init.text)) add(node, `${node.name.getText()}="${init.text}"`);
        else if (ts.isJsxExpression(init) && init.expression && (ts.isStringLiteral(init.expression) || ts.isNoSubstitutionTemplateLiteral(init.expression)) && isCopy(init.expression.text))
          add(node, `${node.name.getText()}={"${init.expression.text}"}`);
      } else if (ts.isPropertyAssignment(node) && ATTRS.has(node.name.getText()) && (ts.isStringLiteral(node.initializer) || ts.isNoSubstitutionTemplateLiteral(node.initializer)) && isCopy(node.initializer.text)) {
        add(node, `${node.name.getText()}: "${node.initializer.text}"`);
      } else if (ts.isJsxExpression(node) && node.expression && ts.isConditionalExpression(node.expression)) {
        for (const branch of [node.expression.whenTrue, node.expression.whenFalse]) {
          if ((ts.isStringLiteral(branch) || ts.isNoSubstitutionTemplateLiteral(branch)) && isCopy(branch.text)) add(branch, `"${branch.text}"`);
        }
      } else if (
        ts.isTemplateExpression(node) &&
        node.parent &&
        ts.isJsxExpression(node.parent) &&
        !(node.parent.parent && ts.isJsxAttribute(node.parent.parent) && /className|class/.test(node.parent.parent.name.getText()))
      ) {
        const head = node.head.text; const tails = node.templateSpans.map((s) => s.literal.text).join(" ");
        if (isCopy(head + " " + tails)) add(node, "`" + head + "${…}" + tails + "`");
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
    if (hits.length) results.set(relative(process.cwd(), file), hits);
  }
}

let total = 0;
const sorted = [...results.entries()].sort((a, b) => b[1].length - a[1].length);
for (const [file, hits] of sorted) {
  total += hits.length;
  if (countOnly) console.log(`${String(hits.length).padStart(4)}  ${file}`);
  else {
    console.log(`\n${file} (${hits.length})`);
    for (const h of hits) console.log(`  ${String(h.line).padStart(4)}: ${h.value.slice(0, 110)}`);
  }
}
console.log(`\n${total} hardcoded strings in ${results.size} files`);

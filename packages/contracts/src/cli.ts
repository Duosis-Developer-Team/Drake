#!/usr/bin/env node
/**
 * drake-validate — validate Drake contract documents.
 *
 * Usage:
 *   drake-validate [--schema drake-project|tenant-snapshot|backup-event] <file...>
 *
 * Exit codes:
 *   0 all files valid
 *   1 at least one file invalid
 *   2 usage or I/O error
 *
 * Output is line-oriented and stable for CI. Error messages never echo
 * matched values (only paths and rule identifiers).
 */
import { readFileSync } from "node:fs";

import { validateContent, type SchemaName } from "./validator.js";

const SCHEMAS: SchemaName[] = ["drake-project", "tenant-snapshot", "backup-event"];

function usage(): void {
  process.stderr.write(
    `usage: drake-validate [--schema ${SCHEMAS.join("|")}] <file...>\n`,
  );
}

function main(argv: string[]): number {
  let schema: SchemaName = "drake-project";
  const files: string[] = [];

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--schema") {
      const value = argv[i + 1];
      if (!value || !SCHEMAS.includes(value as SchemaName)) {
        usage();
        return 2;
      }
      schema = value as SchemaName;
      i += 1;
    } else if (arg === "--help" || arg === "-h") {
      usage();
      return 0;
    } else {
      files.push(arg);
    }
  }

  if (files.length === 0) {
    usage();
    return 2;
  }

  let failed = false;
  for (const file of files) {
    let content: string;
    try {
      content = readFileSync(file, "utf8");
    } catch {
      process.stderr.write(`ERROR ${file}: cannot read file\n`);
      return 2;
    }
    const result = validateContent(content, schema);
    if (result.valid) {
      process.stdout.write(`OK ${file}\n`);
    } else {
      failed = true;
      process.stdout.write(`INVALID ${file}\n`);
      for (const issue of result.issues) {
        process.stdout.write(`  - [${issue.rule}] ${issue.path}: ${issue.message}\n`);
      }
    }
  }
  return failed ? 1 : 0;
}

process.exit(main(process.argv.slice(2)));

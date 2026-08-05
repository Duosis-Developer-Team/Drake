import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2019Import from "ajv/dist/2019.js";
import addFormatsImport from "ajv-formats";
import { parse as parseYaml } from "yaml";

import { checkPolicy, type PolicyFinding } from "./policy.js";

export type SchemaName = "drake-project" | "tenant-snapshot" | "backup-event";

export interface ValidationIssue {
  /** Instance path of the problem ("" = document root). */
  path: string;
  /** "schema" for JSON Schema violations, otherwise the policy rule id. */
  rule: string;
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  issues: ValidationIssue[];
}

const SCHEMA_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "schemas");

const SCHEMA_FILES: Record<SchemaName, string> = {
  "drake-project": "drake-project.schema.json",
  "tenant-snapshot": "tenant-snapshot.schema.json",
  "backup-event": "backup-event.schema.json",
};

// CJS/ESM interop under NodeNext: the default import of these CommonJS
// modules is the module namespace; the constructor/function is on `.default`.
const Ajv2019 = Ajv2019Import.default;
const addFormats = addFormatsImport.default;

// strictRequired is disabled because the project schema legitimately uses
// if/then conditional `required` (kubernetes runtime => clusterRef/namespace).
const ajv = new Ajv2019({ allErrors: true, strict: true, strictRequired: false });
addFormats(ajv);

const compiled = new Map<SchemaName, ReturnType<typeof ajv.compile>>();

function getValidator(schema: SchemaName): ReturnType<typeof ajv.compile> {
  let validate = compiled.get(schema);
  if (!validate) {
    const raw = readFileSync(join(SCHEMA_DIR, SCHEMA_FILES[schema]), "utf8");
    validate = ajv.compile(JSON.parse(raw) as object);
    compiled.set(schema, validate);
  }
  return validate;
}

/** Parse a YAML or JSON document. YAML is a superset here; JSON parses as YAML. */
export function parseDocument(content: string): unknown {
  return parseYaml(content);
}

/**
 * Validate a parsed document against a named Drake contract schema,
 * then apply content policy checks (credential values, inline SQL,
 * plaintext endpoints). Both must pass.
 */
export function validateDocument(document: unknown, schema: SchemaName): ValidationResult {
  const issues: ValidationIssue[] = [];

  const validate = getValidator(schema);
  if (!validate(document)) {
    for (const error of validate.errors ?? []) {
      issues.push({
        path: error.instancePath === "" ? "(root)" : error.instancePath,
        rule: "schema",
        message: `${error.message ?? "schema violation"}${
          error.keyword === "additionalProperties"
            ? ` (${String((error.params as { additionalProperty?: string }).additionalProperty)})`
            : ""
        }`,
      });
    }
  }

  const policyFindings: PolicyFinding[] = checkPolicy(document);
  for (const finding of policyFindings) {
    issues.push({ path: finding.path, rule: finding.rule, message: finding.message });
  }

  return { valid: issues.length === 0, issues };
}

/** Convenience: parse and validate file content in one call. */
export function validateContent(content: string, schema: SchemaName): ValidationResult {
  let document: unknown;
  try {
    document = parseDocument(content);
  } catch (error) {
    return {
      valid: false,
      issues: [
        {
          path: "(root)",
          rule: "parse",
          message: `document is not valid YAML/JSON: ${(error as Error).name}`,
        },
      ],
    };
  }
  if (document === null || typeof document !== "object") {
    return {
      valid: false,
      issues: [{ path: "(root)", rule: "parse", message: "document must be a mapping/object" }],
    };
  }
  return validateDocument(document, schema);
}

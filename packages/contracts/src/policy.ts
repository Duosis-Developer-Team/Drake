/**
 * Content policy checks that run AFTER JSON Schema validation.
 *
 * These protect the manifest contract's security invariants:
 * manifests carry observability *intent* only — never credential values,
 * never raw SQL, never plaintext transport endpoints.
 *
 * Important distinction: a credential *reference* (for example the name of a
 * secret object in `connectionSecretRef`) is legitimate and allowed. Only
 * credential *values* are rejected. Findings never echo the offending value.
 */

export interface PolicyFinding {
  /** JSON path of the offending value, e.g. spec.services[0].workloadSelector.connection */
  path: string;
  /** Stable rule identifier. */
  rule: string;
  /** Human-readable reason. Never contains the matched value. */
  message: string;
}

interface PolicyRule {
  id: string;
  message: string;
  pattern: RegExp;
}

const VALUE_RULES: PolicyRule[] = [
  {
    id: "credential-in-url",
    message: "URL value embeds inline credentials (user:password@host). Use a secret reference name instead.",
    pattern: /:\/\/[^/\s@:]+:[^@\s]+@/,
  },
  {
    id: "credential-assignment",
    message:
      "Value looks like a credential assignment (password/token/key=...). Use a secret reference name instead.",
    pattern:
      /\b(?:password|passwd|pwd|api[_-]?key|secret[_-]?key|client[_-]?secret|access[_-]?key|auth[_-]?token)\b\s*[:=]\s*\S+/i,
  },
  {
    id: "private-key-material",
    message: "Value contains private key material.",
    pattern: /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  },
  {
    id: "bearer-token",
    message: "Value contains a bearer token.",
    pattern: /\bbearer\s+[a-z0-9._~+/=-]{20,}/i,
  },
  {
    id: "cloud-access-key-id",
    message: "Value matches a cloud access key identifier pattern.",
    pattern: /\bAKIA[0-9A-Z]{16}\b/,
  },
  {
    id: "inline-sql",
    message: "Value contains raw inline SQL. Manifests must not embed SQL.",
    pattern:
      /\b(?:select\s+[\s\S]+\s+from\s+|insert\s+into\s+|update\s+\S+\s+set\s+|delete\s+from\s+|drop\s+(?:table|database)\s+|truncate\s+table\s+)/i,
  },
  {
    id: "plaintext-endpoint",
    message: "Value configures a plaintext http:// endpoint. Cross-boundary transport must use TLS.",
    pattern: /\bhttp:\/\//i,
  },
];

const KEY_RULES: PolicyRule[] = [
  {
    id: "insecure-flag",
    message: "Field name suggests disabling transport security, which is not allowed in manifests.",
    pattern: /^(?:insecure(?:[_-]skip[_-]verify)?|skip[_-]?verify|disable[_-]?tls|verify[_-]?ssl)$/i,
  },
];

function walk(
  node: unknown,
  path: string,
  visit: (path: string, key: string | null, value: unknown) => void,
): void {
  if (Array.isArray(node)) {
    node.forEach((item, index) => walk(item, `${path}[${index}]`, visit));
    return;
  }
  if (node !== null && typeof node === "object") {
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      const childPath = path === "" ? key : `${path}.${key}`;
      visit(childPath, key, value);
      walk(value, childPath, visit);
    }
    return;
  }
}

/** Scan a parsed manifest for policy violations. */
export function checkPolicy(document: unknown): PolicyFinding[] {
  const findings: PolicyFinding[] = [];
  walk(document, "", (path, key, value) => {
    if (key !== null) {
      for (const rule of KEY_RULES) {
        if (rule.pattern.test(key)) {
          findings.push({ path, rule: rule.id, message: rule.message });
        }
      }
    }
    if (typeof value === "string") {
      for (const rule of VALUE_RULES) {
        if (rule.pattern.test(value)) {
          findings.push({ path, rule: rule.id, message: rule.message });
        }
      }
    }
  });
  return findings;
}

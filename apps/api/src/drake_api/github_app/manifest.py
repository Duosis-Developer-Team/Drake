"""Server-side validation of `.drake/project.yaml` (ADR-0007).

The browser may validate for feedback, but the API is the authority: a
manifest that reaches the catalog has been checked here. Both sides read
the SAME canonical JSON Schema from `packages/contracts/schemas`, and the
content-policy rules below mirror `packages/contracts/src/policy.ts` rule
for rule — a parity test proves the two agree on every contract fixture.

Nothing in this module executes anything. YAML is parsed with the safe
loader, the Node contract CLI is never invoked from a request, and no
finding ever echoes the value that triggered it.
"""

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

# The repository root, from apps/api/src/drake_api/github_app/manifest.py.
_CONTRACTS = Path(__file__).resolve().parents[5] / "packages" / "contracts"
SCHEMA_PATH = _CONTRACTS / "schemas" / "drake-project.schema.json"

MANIFEST_PATH = ".drake/project.yaml"
# A manifest is a statement of intent, not a data file.
MAX_MANIFEST_BYTES = 128 * 1024

# Bounds on the document SHAPE, checked while parsing rather than after.
MAX_YAML_DEPTH = 12
MAX_YAML_NODES = 4_000


class _StrictLoader(yaml.SafeLoader):
    """`SafeLoader`, plus the thing SafeLoader does not refuse.

    **Duplicate keys.** PyYAML takes the last one silently. In a manifest
    that means a reviewer approves the value they can see and the parser
    uses a different one further down the file — the easiest way there is
    to get something past a review.
    """

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _check_shape(node: Any, depth: int = 0, counter: list[int] | None = None) -> None:
    """Depth and node bounds, so a pathological document is cheap to refuse."""
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if depth > MAX_YAML_DEPTH:
        raise ValueError("Manifest nesting exceeds the supported depth.")
    if counter[0] > MAX_YAML_NODES:
        raise ValueError("Manifest contains more nodes than the contract allows.")
    if isinstance(node, dict):
        for value in node.values():
            _check_shape(value, depth + 1, counter)
    elif isinstance(node, list):
        for item in node:
            _check_shape(item, depth + 1, counter)


@dataclass(frozen=True)
class Finding:
    """One reason a manifest was refused.

    `path` locates the problem and `rule` identifies it; `message` explains
    it. None of them ever carries the offending value — findings are
    rendered in the UI and written to audit metadata.
    """

    path: str
    rule: str
    message: str

    def as_json(self) -> dict[str, str]:
        return {"path": self.path, "rule": self.rule, "message": self.message}


@dataclass
class ValidationResult:
    valid: bool
    findings: list[Finding] = field(default_factory=list)
    document: dict[str, Any] | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "findings": [finding.as_json() for finding in self.findings],
        }


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


# --- content policy ------------------------------------------------------
# Mirrors packages/contracts/src/policy.ts. Keep the rule ids identical:
# the parity test compares them across both implementations.

_VALUE_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "credential-in-url",
        "URL value embeds inline credentials (user:password@host). "
        "Use a secret reference name instead.",
        re.compile(r"://[^/\s@:]+:[^@\s]+@"),
    ),
    (
        "credential-assignment",
        "Value looks like a credential assignment (password/token/key=...). "
        "Use a secret reference name instead.",
        re.compile(
            r"\b(?:password|passwd|pwd|api[_-]?key|secret[_-]?key|client[_-]?secret"
            r"|access[_-]?key|auth[_-]?token)\b\s*[:=]\s*\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "private-key-material",
        "Value contains private key material.",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ),
    (
        "bearer-token",
        "Value contains a bearer token.",
        re.compile(r"\bbearer\s+[a-z0-9._~+/=-]{20,}", re.IGNORECASE),
    ),
    (
        "cloud-access-key-id",
        "Value matches a cloud access key identifier pattern.",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "inline-sql",
        "Value contains raw inline SQL. Manifests must not embed SQL.",
        re.compile(
            r"\b(?:select\s+[\s\S]+\s+from\s+|insert\s+into\s+|update\s+\S+\s+set\s+"
            r"|delete\s+from\s+|drop\s+(?:table|database)\s+|truncate\s+table\s+)",
            re.IGNORECASE,
        ),
    ),
    (
        "plaintext-endpoint",
        "Value configures a plaintext http:// endpoint. Cross-boundary transport must use TLS.",
        re.compile(r"\bhttp://", re.IGNORECASE),
    ),
)

_KEY_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "insecure-flag",
        "Field name suggests disabling transport security, which is not allowed in manifests.",
        re.compile(
            r"^(?:insecure(?:[_-]skip[_-]verify)?|skip[_-]?verify|disable[_-]?tls|verify[_-]?ssl)$",
            re.IGNORECASE,
        ),
    ),
)


def _walk(node: Any, path: str, visit: Any) -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            _walk(item, f"{path}[{index}]", visit)
        return
    if isinstance(node, dict):
        for key, value in node.items():
            child = key if path == "" else f"{path}.{key}"
            visit(child, str(key), value)
            _walk(value, child, visit)


def check_policy(document: Any) -> list[Finding]:
    """Scan a parsed manifest for policy violations."""
    findings: list[Finding] = []

    def visit(path: str, key: str, value: Any) -> None:
        for rule_id, message, pattern in _KEY_RULES:
            if pattern.search(key):
                findings.append(Finding(path=path, rule=rule_id, message=message))
        if isinstance(value, str):
            for rule_id, message, pattern in _VALUE_RULES:
                if pattern.search(value):
                    findings.append(Finding(path=path, rule=rule_id, message=message))

    _walk(document, "", visit)
    return findings


def _schema_path(error: Any) -> str:
    parts: list[str] = []
    for token in error.absolute_path:
        if isinstance(token, int):
            parts.append(f"[{token}]")
        else:
            parts.append(f".{token}" if parts else str(token))
    return "".join(parts) or "(document)"


def validate_document(document: Any) -> ValidationResult:
    """Schema then policy. Both run; the caller sees every reason at once."""
    findings: list[Finding] = []
    if not isinstance(document, dict):
        return ValidationResult(
            valid=False,
            findings=[
                Finding(
                    path="(document)",
                    rule="not-an-object",
                    message="A manifest must be a YAML mapping.",
                )
            ],
        )

    for error in sorted(_validator().iter_errors(document), key=lambda item: list(item.path)):
        findings.append(
            Finding(
                path=_schema_path(error),
                rule="schema",
                # `error.message` describes the constraint. For an
                # `additionalProperties` violation it names the offending
                # KEY, which is contract vocabulary, not a value.
                message=error.message,
            )
        )
    findings.extend(check_policy(document))
    return ValidationResult(
        valid=not findings, findings=findings, document=document if not findings else None
    )


def validate_content(content: str) -> ValidationResult:
    """Parse and validate raw manifest text.

    `_StrictLoader` extends the SAFE loader: it constructs plain Python
    values and cannot instantiate arbitrary objects the way the full loader
    can — which matters because this text came from a repository — and it
    additionally refuses duplicate keys.
    """
    raw = content.encode("utf-8")
    if len(raw) > MAX_MANIFEST_BYTES:
        return ValidationResult(
            valid=False,
            findings=[
                Finding(
                    path=MANIFEST_PATH,
                    rule="manifest-too-large",
                    message=(f"Manifest exceeds the {MAX_MANIFEST_BYTES // 1024} KiB limit."),
                )
            ],
        )
    try:
        document = yaml.load(content, Loader=_StrictLoader)  # noqa: S506 - a SafeLoader subclass
        _check_shape(document)
    except ValueError as error:
        return ValidationResult(
            valid=False,
            findings=[Finding(path=MANIFEST_PATH, rule="manifest-shape", message=str(error))],
        )
    except yaml.YAMLError as error:
        # The parser's message can quote the offending line, so only the
        # position is kept.
        mark = getattr(error, "problem_mark", None)
        where = f"line {mark.line + 1}" if mark is not None else "unknown position"
        # Named specifically: a duplicate key is a review-integrity problem,
        # not a syntax error, and the two deserve different words.
        duplicate = "duplicate key" in str(getattr(error, "problem", ""))
        return ValidationResult(
            valid=False,
            findings=[
                Finding(
                    path=MANIFEST_PATH,
                    rule="duplicate-key" if duplicate else "yaml-parse",
                    message=(
                        f"Manifest defines the same key twice ({where}). A reviewer "
                        "would see one value and the parser would use another."
                        if duplicate
                        else f"Manifest is not valid YAML ({where})."
                    ),
                )
            ],
        )
    return validate_document(document)


def check_repository_identity(
    document: dict[str, Any], owner: str, name: str, default_branch: str
) -> list[Finding]:
    """The manifest must describe the repository it was read from.

    A manifest naming a different repository is either a copy-paste error
    or an attempt to onboard something else under this repository's
    identity; both are refusals rather than something to normalise away.
    """
    repository = (document.get("spec") or {}).get("repository") or {}
    findings: list[Finding] = []
    if str(repository.get("owner") or "").lower() != owner.lower():
        findings.append(
            Finding(
                path="spec.repository.owner",
                rule="repository-identity",
                message="Manifest owner does not match the repository it was read from.",
            )
        )
    if str(repository.get("name") or "").lower() != name.lower():
        findings.append(
            Finding(
                path="spec.repository.name",
                rule="repository-identity",
                message="Manifest name does not match the repository it was read from.",
            )
        )
    declared_branch = str(repository.get("defaultBranch") or "")
    if declared_branch and default_branch and declared_branch != default_branch:
        findings.append(
            Finding(
                path="spec.repository.defaultBranch",
                rule="repository-identity",
                message="Manifest default branch does not match the observed default branch.",
            )
        )
    return findings

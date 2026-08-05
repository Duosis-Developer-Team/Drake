# ADR-0006: Static-only GitHub repository analysis

**Status:** Accepted

## Context

Repository onboarding requires inspecting repo contents. Executing repository
code (builds, hooks, scripts, package managers) would hand code execution to
anyone who can push to an onboarded repo.

## Decision

The repository importer performs static file/metadata analysis only. It never
runs shell commands, package installs, builds, tests, git hooks, or any code
from the analyzed repository. Analysis respects file-count/byte/time budgets
and allowlisted paths. Content matching secret patterns is not indexed; it
produces a security warning instead.

## Consequences

- Onboarding is safe against hostile repositories by construction.
- Some insight (e.g. resolved dependency trees) is out of scope; the manifest
  contract (ADR-0007) supplies intent instead.
- A malicious-fixture test proving non-execution is part of the importer's
  acceptance gate.

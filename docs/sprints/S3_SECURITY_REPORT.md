# Sprint 3 — Security Report

Public-safe summary of the telemetry security posture. Everything listed
as enforced is backed by executed tests (see S3_TEST_REPORT).

## 1. No user queries, ever

Users cannot submit PromQL, metric names, label names, regexes, operators,
or fragments. The request model is `extra=forbid`: `query`, `promql`,
`metric_name`, `provider_url`, and `config_ref` have no field to arrive in
and are 422 on sight. Every query compiles from a versioned,
repository-controlled template; matcher label names come from the
registry, matcher values from authoritative catalog rows through one
central exact-match escaper. Injection attempts (quotes, backslashes,
newlines, operator smuggling) are neutralized or refused — negative-tested.

## 2. Authorization before everything

The broker's order is fixed: session → shape → authoritative scope lookup
→ `telemetry.query` effective grants → catalog relationships → template
compatibility → integration resolution → cache → budgets → provider.
Unauthorized queries produce **zero provider calls and zero cache reads**
(proven with call counters and spies), and out-of-scope/nonexistent
targets are consistent 404s — no timing, cache, or error side-channel
reveals scope existence. Project grants never reach cluster telemetry and
vice versa.

## 3. Fail-closed budgets

Global ceilings (30d range, 10s timeout, 200 series, 20k points,
4 concurrent per principal, 8 per target scope) that templates may only
narrow. Tiny steps are raised server-side and disclosed
(`step_adjusted`); huge ranges are refused. Concurrency uses atomic Redis
leases (unique token, bounded TTL, self-release only, stale sweep).
**A Redis outage refuses queries with a typed retryable 503 — budgets are
never bypassed.**

## 4. Provider boundary (SSRF)

Provider endpoints exist only as server-owned connector configuration
(`config_ref` → URL via settings/external secrets); requests can never
supply one. The adapter refuses non-http(s) schemes, embedded
credentials, metadata/link-local/multicast/unspecified targets (always),
plaintext HTTP and loopback outside local/test; follows no redirects;
bounds body size (2 MiB) and timeouts; validates JSON strictly; and
redacts upstream errors to bounded machine-readable codes. Provider
responses are normalized — never proxied — and series labels outside the
template's output allowlist fail the response closed.

## 5. Data boundaries

Raw samples, PromQL, and provider responses are never written to
PostgreSQL. Redis holds only normalized safe envelopes (content-checked
before write; negative-tested for PromQL/URLs/config refs) plus lease
bookkeeping. The integration observation projection stores states,
timestamps, and DB-CHECK-backed bounded error codes only. Stale last-good
data is always labelled `stale` with its `as_of` — never re-badged
healthy. Non-finite values become nulls with a `partial` flag, never `0`.

## 6. Browser boundary

The browser talks only to the Drake API. The static provider-access guard
now also fails CI if `/api/v1/query*`, provider ports, config references,
or PromQL mentions appear in browser code. Charts are dependency-free
inline SVG; no external asset or endpoint enters the bundle.

## 7. Drake's own metrics

Broker metrics are bounded: labels are registry template keys and fixed
enums only — never principals, tenants, scope refs, URLs, correlation
IDs, or raw errors. `/v1/internal/metrics` must not be exposed on public
ingress (deployment-gate note; no deployment exists this sprint).

## 8. Scanning posture

gitleaks default rules with **no path exclusions**; two narrow line-shape
allowlists cover registry key identifier fields (shapes a credential
cannot take), and the canary regression still proves fixture directories
are scanned. OSV clean on the digest-pinned scanner. The rendered Helm
package is policy-checked: no LoadBalancer/NodePort/Ingress, no
credential-shaped secret content, no wildcard-everything RBAC.

## 9. Known security work ahead

- Real dev-cluster apply stays behind an explicit operator-approval gate.
- Agent enrollment/mTLS (Sprint 4), GitHub App, webhook signing arrive
  with their own gates.
- Session inactivity timeout and auth rate limiting remain tracked
  hardening items.
- One source-repository security review item remains tracked privately
  and gates the corresponding integration sprint (unchanged).

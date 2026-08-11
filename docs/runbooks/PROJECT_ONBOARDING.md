# Onboarding a project

The chain, and who is authoritative at each step:

```
GitHub App → repository discovery → .drake/project.yaml
  → static analysis → reviewable plan → approval
  → catalog reconciliation → existing Drake modules
```

```
Drake catalog        authoritative runtime projection
.drake/project.yaml  versioned repository INTENT
GitHub discovery     evidence
Kubernetes agent     observed runtime state
Telemetry providers  observed telemetry state
```

A manifest says what a repository **wants**. It does not get to be right,
it cannot choose infrastructure, and it cannot grant anyone anything.

## Not configured

With no GitHub App, `GET /v1/onboarding/github/status` answers
`not_configured` and lists which reference is absent — never its name and
never its value. No token is minted, no call is made, no repository list is
invented, and nothing cached is shown as fresh. The screen says so in
words: an unconfigured integration and an empty one are different answers,
and only one of them means someone should go configure something.

`github_gitops_pr_enabled` requires `github_app_enabled`; a write path
cannot be switched on while the integration that authenticates it is off.

## The security gate outranks everything

`Duosis-Developer-Team/Datalake-Platform-GUI` is **closed** by the
`manual_env_review` gate set in Sprint 5A over a tracked `.env`. Sprint 11
does not open it. Creating a session for a gated repository is refused
before any credential is read — zero provider calls, zero token mints — and
the gate is re-checked against the current name as well as the stored flag.

The golden path is proven against Drake's own sanitized fixture in
`packages/contracts/onboarding/`, which contains no secret, no file body
and no copy of that repository. Onboarding the real one stays blocked until
the Datalake team completes their remediation; that is their work, not
Drake's.

## Static discovery

Reads an allowlist of metadata files through the Contents API at **one
immutable commit**, under hard budgets.

**Never executed:** repository scripts, `npm install`, `pip install`,
`go mod download`, builds, tests, git hooks, GitHub Actions, Makefile
targets, Docker builds, containers, `helm template`, `kustomize build`,
Terraform, repository executables, package lifecycle hooks. Discovery that
runs the thing it is discovering is not discovery.

**Never read:** `.env` and `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`,
`id_rsa*`, `credentials*`, `secrets*`, cluster config files, binaries,
oversized files, vendor and generated directories, `.git`, `node_modules`,
symlinks, submodules, LFS content. They are not on the allowlist, so they
are never requested — not filtered afterwards.

Budgets: 60 files, 256 KiB per file, 1 MiB total, 80 provider calls, 15
seconds, depth 2. Exceeding any of them produces

```
status = partial     truncated = true
```

and a **blocking** plan item. An incomplete picture is never a green light.

Findings carry `finding_type`, `safe_path`, `confidence`, `evidence_kind`,
`proposed_target` and `review_reason`. A path, never the content at it, and
never the text that triggered a warning.

## The manifest contract

`.drake/project.yaml`, `apiVersion: drake.duosis.com/v1alpha1`, validated
against the canonical JSON Schema in `packages/contracts/schemas` with
`additionalProperties: false` throughout — so a field the contract does not
name is a refusal, not an ignored line.

Refused by the parser before the schema even runs:

- **duplicate keys.** PyYAML takes the last one silently, which means a
  reviewer approves the value they can see and the parser uses another one
  further down the file. That is the easiest way there is to get something
  past a review, so a duplicate key is a named refusal.
- **unbounded shape.** Depth and node count are capped.
- **arbitrary Python objects.** The loader is a `SafeLoader` subclass.

Content policy refuses credentials, inline private keys, bearer tokens,
cloud access keys, inline SQL and plaintext `http://` endpoints. A finding
names the path and the rule, never the offending value.

The manifest must describe the repository it was read from; one naming a
different repository is refused rather than normalised.

## Plan, approval, apply

An analysis is identified by `(repository, commit, analyzer_version)`, so
re-running it returns the same row rather than a second opinion about one
commit.

Plan item actions, and there is deliberately **no `delete`**:

| Action | Meaning |
| --- | --- |
| `create` | Nothing in the catalog matches; this would be added |
| `link` | An existing catalog row matches and would be attached |
| `update_metadata` | The row exists; some metadata would change |
| `no_change` | The catalog already matches |
| `conflict` | Two things match, or the key belongs to another repository |
| `unmapped` | Named something Drake does not have, or has and the manifest dropped |
| `unsupported` | Drake cannot act on this |

The last three **block apply**. Ambiguity is a decision, not a warning:
picking whichever row sorted first would file a service under the wrong
environment and nobody would know.

A service the manifest stopped mentioning appears as `unmapped` with
`catalog_only`. It is never removed — a manifest edit is not evidence that
a running service stopped existing, and a catalog that deletes on a diff
will one day delete on a mistake.

An owner team Drake has not seen is never `unmapped`: the first project any
team owns would otherwise be permanently unonboardable. A team key is a
bounded label on a project and grants nothing; authority comes from RBAC
grants, which no manifest can touch.

Which of the two non-blocking outcomes you get depends on the project, and
the distinction matters because getting it wrong silently dropped owners:

| | |
|---|---|
| New project | `no_change` / `applied_with_parent` — the association is created inside the same transaction as the project |
| Existing project, owner already recorded | `no_change` / `owner_team_already_recorded` |
| Existing project, owner missing | **`create`** — planned as a real add and applied as one |

Adding is purely additive. An owner the manifest omits is never removed, an
existing team is never replaced, and a role is never reassigned — a
manifest edit is not evidence that somebody's recorded ownership decision
was withdrawn.

Approval names an exact **plan version**, and apply re-checks it, plus the
commit and the manifest digest. Any drift refuses with `plan_stale`. A
default-branch `push` webhook marks reviewed plans stale for the same
reason: a review of a commit is not a review of its successor.

Apply is one transaction with **no network calls** — the freshness check
happens before it opens — and is idempotent on
`(session, idempotency_key)`. A retry returns the recorded answer, in full
and including the outcome word, rather than a second project. That check
runs before the approval check, because otherwise a client that merely lost
a response would be told its own successful import was unapproved. The same
key under a *different* plan version is a conflict
(`idempotency_key_reused`), not a fresh apply.

## Doing it from the browser (Sprint 12A.2a)

`/onboarding` is the operator's screen, and it drives exactly the flow
above:

1. **Choose a repository.** The picker lists repositories from
   `GET /v1/onboarding/repositories`, scoped to `onboarding.manage` — the
   permission the Start button needs, not read access. A repository that
   cannot be started says why: a security gate, lost access, or a session
   already open (in which case the button opens that session instead of a
   second one).
2. **Start.** Nothing is created by opening the page, and a
   `?repository_id=` link preselects a repository without acting on it.
3. **Analyse**, then **Analyse again** if the repository moves.
4. **Review.** The plan is grouped by what it would do, and
   `update_metadata` items show `before` / `after` for each field. An
   absent previous value renders as `—`, never as an empty string.
5. **Approve.** The confirmation names the plan version, commit, digest and
   item count. It never shows the manifest.
6. **Apply.** The confirmation says plainly: *this writes the approved plan
   to Drake's catalog; it does not write to the repository.* The result
   card shows all seven counters. A counter a pre-0020 receipt never
   recorded reads **Not recorded**, not `0`.

Every button is a courtesy. The API re-checks the permission against the
session's own scope and re-checks the state under a row lock, so a hidden
button is not a boundary and a visible one is not a promise.

### The idempotency key

The browser generates one key per (session, plan version), keeps it in
component memory, and reuses it across its own retries. A new key on a
retry would describe a new operation, so an apply that committed before the
connection dropped would be applied twice. The same value travels in the
`Idempotency-Key` header and in the request body. It is never written to
`localStorage`, `sessionStorage`, the URL or a log.

## Getting a manifest to commit

`GET /v1/onboarding/sessions/{id}/manifest-draft` returns a manifest built
deterministically from that session's stored analysis — no provider call,
no live read. It is served as an `application/yaml` attachment with
`Cache-Control: no-store`, and the UI offers it as a download rather than
rendering it: a manifest is a file to commit, not markup to put in a page.

This is how an operator adds `.drake/project.yaml` to a repository that has
none. Drake cannot commit it for them: `Contents: write` is not requested,
and no pull-request provider exists yet.

**Production has no GitOps provider at all.** The only implementation is a
recording test double, and a production process is not allowed to hold one
— it would report a pull request that does not exist. So
`github_gitops_pr_enabled` and `gitops_worker_enabled` are refused outside
local/test: a process configured with either does not start, and says which
flag to turn off. Enabling repository writes is Sprint 12B's real provider
plus a separate decision, not a flag change.

## Session states, and what may act from them

Enforced in the API inside the transaction that mutates, holding the
session's row lock — so a double-click, or two operators, cannot both act
on the same state.

| Action | Allowed from |
| --- | --- |
| Analyze | `draft`, `discovery_pending`, `needs_review`, `ready`, `provider_unavailable`, `stale`, `failed` |
| Approve | `ready` only |
| Apply | `approved` only, at exactly `approved_plan_version` |
| Cancel | every non-terminal state above, plus `approved` |
| GitOps request | `needs_review`, `ready`, `approved` |

`imported` and `cancelled` are terminal. A repository is revisited with a
**new** session, which keeps the record of what the old one decided. A
second analyse while one is running is one bounded `409`, not a second
scan.

## GitOps pull requests

Drake never writes to a default branch. When a repository needs a manifest
it does not have, Drake opens a pull request and a human merges it.

```
catalog apply   changes DRAKE
GitOps PR       proposes a change to the REPOSITORY
```

Separate lifecycles, separate permissions, separate audit trails. Merging
a PR is not an import, and an import does not merge a PR.

Branch (`drake/onboarding/<id>`) and path (`.drake/project.yaml`, enforced
by a CHECK constraint) are server-composed; the caller supplies neither,
nor the base repository, nor file content — which is regenerated
deterministically at send time so what is reviewed and what is pushed
cannot diverge. The body is bounded and credential-free.

`pending` is not `active`: a pull request Drake has not created yet is not
open, and a failed one is `failed` with a bounded code.

## Permissions

```
onboarding.view     see sessions, findings and plans
onboarding.manage   create sessions, analyse, approve
onboarding.apply    write the approved plan to the catalog
onboarding.gitops   propose a manifest to a repository
integration.manage  the GitHub installation projection itself
```

Four separate rights on purpose. Seeing a repository is not seeing a
catalog project; reviewing is not applying; and applying to Drake is not
proposing a change to somebody's repository. None was added to a starter
role except Platform Admin and Platform Owner.

Scope filtering happens in SQL before any count, page or filter option.
Anything outside scope is 404, including for a caller who lacks the right
entirely — a 403 would confirm the session exists.

## API

```
GET  /v1/onboarding/github/status      POST /v1/onboarding/sessions
GET  /v1/onboarding/repositories       POST /v1/onboarding/sessions/{id}/analyze
GET  /v1/onboarding/sessions           POST /v1/onboarding/sessions/{id}/approve
GET  /v1/onboarding/sessions/{id}      POST /v1/onboarding/sessions/{id}/apply
GET  /v1/onboarding/sessions/{id}/findings
GET  /v1/onboarding/sessions/{id}/plan POST /v1/onboarding/sessions/{id}/cancel
GET  /v1/onboarding/sessions/{id}/manifest-draft
GET  /v1/onboarding/filters            POST /v1/onboarding/sessions/{id}/gitops-request
POST /v1/integrations/github/webhook
```

The Sprint 5B path under
`/v1/integrations/github/repositories/{id}/onboarding*` answers **410
Gone** with `legacy_onboarding_retired`. It imported without a plan, an
approval, a digest or a receipt; this path exists so that none of those is
optional.

No endpoint accepts a repository URL, an owner/name pair, a branch, a file
path, a manifest body, a plan item, a catalog id, a cluster, a permission
or a provider address. A repository is chosen from Drake's own projection
by its row id.

## After an import

The imported catalog identity is the one every other Drake module already
uses: Cluster Inventory, curated metrics, the Query Broker, deployment
correlation, Protection Center, alerts, incidents and SLOs.

Integrations are registered as honest placeholders and report
`not_configured` until an operator wires them. Nothing is fabricated to
make a fresh project look healthy — the available states are
`not_configured`, `unmapped`, `insufficient_data`, `stale` and `unknown`.

## Troubleshooting

**A session will not analyse.** Check the security gate and the
reconciliation state; both refuse before any provider call.

**The plan says `unmapped` for a cluster.** Clusters are operator-
registered infrastructure. Register it, then analyse again.

**Apply says `plan_stale`.** Someone pushed to the default branch, or the
manifest changed, after the review. Analyse again and re-approve.

**Apply says `not_approved` after a successful import.** It should not —
the idempotency check runs first. If it does, the idempotency key differs
from the one that succeeded.

**A pull request stays `pending`.** The GitOps worker is off
(`gitops_worker_enabled`), or GitHub is unreachable. It is proposing
nothing meanwhile, which is why it does not say open.

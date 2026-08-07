# Drake GitHub App — endpoint → permission matrix (Sprint 5A)

Every permission below is justified by an endpoint Drake actually calls.
Anything not listed is **not requested**. All levels are **read**; Sprint
5A requests no write permission of any kind.

Sources: GitHub REST API reference and "Permissions required for GitHub
Apps" (API version `2022-11-28`), plus "Generating a JSON Web Token (JWT)
for a GitHub App".

## App-level endpoints (authenticated with the app JWT)

These use the RS256 app JWT, not an installation token, and carry no
fine-grained permission requirement.

| Endpoint | Purpose in Drake | Auth | Required in 5A |
|---|---|---|---|
| `GET /app/installations` | discover installations of the app | app JWT | yes |
| `GET /app/installations/{installation_id}` | installation detail, account identity, suspension state | app JWT | yes |
| `POST /app/installations/{installation_id}/access_tokens` | mint a short-lived, repository- and permission-scoped installation token | app JWT | yes |

JWT contract (per GitHub's documentation): `RS256`; `iat` set 60 seconds
in the past to absorb clock drift; `exp` no more than 10 minutes in the
future; `iss` is the **client id** (app id accepted for older operator
configuration).

## Repository endpoints (authenticated with an installation token)

| Endpoint | Purpose in Drake | Permission | Level | Webhook events that trigger a refresh | Required in 5A |
|---|---|---|---|---|---|
| `GET /installation/repositories` | list repositories the installation can see | Metadata | read | `installation`, `installation_repositories` | yes |
| `GET /repos/{owner}/{repo}` | permanent repository id, owner, name, visibility, archived/disabled, **default branch** | Metadata | read | `repository`, `installation_repositories` | yes |
| `GET /repos/{owner}/{repo}/branches/{branch}/protection` | classic branch protection: required status checks (+ `strict`), enforce admins, force-push and deletion protection, required reviews | Administration | read | `repository` | yes |
| `GET /repos/{owner}/{repo}/rules/branches/{branch}` | the rules **actually in effect** on the default branch, already resolved across repository and organization rulesets and already filtered to active enforcement | Metadata | read | `repository` | yes |
| `GET /repos/{owner}/{repo}/rulesets` | ruleset **summaries** only — used for attribution, never as rule evidence | Administration | read | `repository` | no |
| `GET /repos/{owner}/{repo}/actions/workflows` | workflow inventory (names, paths, state) for CI-gate presence | Actions | read | `repository` | yes |
| `GET /repos/{owner}/{repo}/environments` | environment inventory, to locate production-like environments | Actions | read | `repository` | yes |
| `GET /repos/{owner}/{repo}/environments/{environment_name}` | environment protection rules: required reviewers, wait timer, deployment branch policy | Actions | read | `repository` | yes |
| `GET /repos/{owner}/{repo}/vulnerability-alerts` | whether Dependabot alerts are enabled | Administration | read | `repository` | optional |
| `GET /repos/{owner}/{repo}/secret-scanning/alerts` | whether secret scanning is enabled and answering | Secret scanning alerts | read | — | optional |
| `GET /repos/{owner}/{repo}/code-scanning/alerts` | whether code scanning is enabled and answering | Code scanning alerts | read | — | optional |

"Optional" means the rule that consumes it degrades to `UNKNOWN` when the
permission is absent — it never degrades to `PASS`.

## Resulting permission request

| Permission | Level | Justification |
|---|---|---|
| Metadata | read | mandatory for any GitHub App; repository identity and default branch |
| Administration | read | branch protection and rulesets are only readable through it; **read only — no write** |
| Actions | read | workflow and environment inventory, environment protection rules |
| Secret scanning alerts | read | security-scan gate evidence (optional) |
| Code scanning alerts | read | security-scan gate evidence (optional) |

## Webhook event subscriptions

| Event | Why Drake needs it |
|---|---|
| `installation` | created / deleted / suspend / unsuspend — installation lifecycle and access loss |
| `installation_repositories` | repositories added to or removed from the installation |
| `repository` | renamed, transferred, privatised, archived, deleted |

No other event is subscribed. Adding one requires a matching consumer
and an update to this matrix.

## Explicitly forbidden in Sprint 5A

Not requested, not used, and rejected in review if proposed:

- **Administration: write** — would allow changing branch protection or
  rulesets. Drake evaluates; it does not remediate.

## Permission note: effective rules need only Metadata

`GET /repos/{owner}/{repo}/rules/branches/{branch}` requires **Metadata:
read**, not Administration. `Administration: read` is still required for
the classic branch-protection endpoint and for the ruleset management
endpoints, so it remains in the requested set — but an unreadable
effective-rules response must be reported as a `metadata:read` gap, never
as `administration:read`. Naming the wrong permission sends an operator to
grant something that would not have fixed anything.

## Why the effective-rules endpoint, not the ruleset list

`GET /repos/{owner}/{repo}/rulesets` returns ruleset *summaries*: `id`,
`name`, `target`, `source_type`, `enforcement`, timestamps. There is **no
`rules` member in that response**. Treating an entry from it as evidence
that a particular rule is configured reads something the payload never
contained, and an absent `rules` key must never be read as "no rules".

`GET /repos/{owner}/{repo}/rules/branches/{branch}` answers the question
the policy engine actually asks: which rules apply to *this* branch. The
endpoint already excludes rulesets that are disabled, that target tags,
or whose conditions do not cover the branch, and it already includes
rules inherited from organization-level rulesets. An unreadable response
is `UNKNOWN`, never a pass.
- **Contents: write** (and read) — no source access is needed to answer
  the Sprint 5A questions; workflow *file* parsing is deliberately out
  of scope.
- **Workflows: write**, **Actions: write** — no workflow dispatch, no
  workflow file changes.
- **Deployments: write** — Drake never creates a deployment.
- **Checks / Statuses: write** — Drake never writes a check or commit
  status result.
- **Pull requests: write** — Drake never opens or merges a pull request.
- **Secrets / Variables / Environments: write** — Drake never creates or
  changes a secret, variable, or environment.
- **Members / Organization administration** — out of scope.

## Behaviour when granted permissions are narrower than requested

Drake never escalates. If the live installation is missing a permission
a rule needs, that rule is `UNKNOWN` with an explicit reason, the
repository moves to `DEGRADED` (or `BLOCKED` when a required permission
is absent), and the operator sees exactly which permission is missing —
with no secret material in the message.

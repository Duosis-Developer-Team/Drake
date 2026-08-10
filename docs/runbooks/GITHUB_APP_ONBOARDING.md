# Runbook — GitHub App connection and repository onboarding

How an operator connects Drake to GitHub, what Drake does with that
access, and how to rotate or revoke it. Sprint 5A is read-only: nothing
in this runbook changes a repository setting.

## What the operator must supply

Drake never generates, requests, or stores these values in Git. Each is
supplied out of band and referenced by name.

| Input | Where it goes | Notes |
|---|---|---|
| App **client id** (or app id) | `DRAKE_GITHUB_APP_CLIENT_ID` | GitHub recommends the client id as the JWT issuer |
| Private key **PEM** | secret store; path in `DRAKE_GITHUB_APP_PRIVATE_KEY_FILE` | the file holds the PEM; Drake only holds the path |
| Webhook **secret** | secret store; path in `DRAKE_GITHUB_WEBHOOK_SECRET_FILE` | used only to verify signatures |
| Organization / owner | fixed: `Duosis-Developer-Team` | webhooks from any other owner are refused |
| Installation id | discovered from the installation webhook | no manual entry needed |
| Public webhook URL | GitHub App settings | must terminate at the Drake API |
| Repository selection | GitHub App settings | select repositories explicitly; avoid "all" |
| Permission + event approval | GitHub App settings | must match `docs/github-app-permission-matrix.md` |

**PEM encoding.** Store the key exactly as GitHub issued it: a PEM block
with real newlines, `-----BEGIN …PRIVATE KEY-----` first line, trailing
newline at the end. Do not base64 the file again, do not convert
newlines to `\n` escapes, and do not paste it into an environment
variable. File permissions should be `0600` and owned by the API user.

## Startup validation

Outside `local`/`test`, the API refuses to start when the feature is on
but any of these is missing: the private key reference, the webhook
secret reference, an app identity, or an `https://` API base URL. A JWT
lifetime above GitHub's 10-minute ceiling is refused in every
environment. This is deliberate: a half-configured integration must not
run at all.

`GET /v1/integrations/github/status` reports readiness and names the
missing inputs — never their values.

## Connecting (order matters)

1. Register the GitHub App with **exactly** the permissions and events in
   `docs/github-app-permission-matrix.md` (all read; three events).
2. Generate the private key and place it in the secret store.
3. Generate the webhook secret and place it in the secret store.
4. Point the webhook URL at the Drake API and set the secret.
5. Set the environment references and restart the API.
6. Install the App on the organization, selecting repositories
   explicitly. **Do not select `Datalake-Platform-GUI`** while its
   security gate is open.
7. Confirm the installation webhook arrived: the installation appears
   with its repositories in `DISCOVERED`.
8. Reconcile a repository (dry run) and read the policy snapshot.

## Rotation

**Private key.** Generate a second key in GitHub before removing the
first. Write the new PEM to the secret store, restart the API, confirm a
reconcile succeeds, then delete the old key in GitHub. Drake caches no
JWT longer than 10 minutes, so no further action is needed.

**Webhook secret.** GitHub allows one secret at a time, so a rotation has
a brief window where in-flight deliveries fail signature verification.
Rotate during a quiet period: update GitHub, update the secret store,
restart. Deliveries refused during the window are visible in the audit
log as `github.webhook.rejected`; GitHub retries them, and replay is
idempotent, so nothing is lost as long as the retry lands after the
restart.

## Revocation, suspension, uninstall

Suspending or uninstalling the App is safe and reversible from Drake's
side: repositories move to `DISABLED` with the reason recorded, and
**nothing is deleted**. Re-installing rediscovers them onto the same rows
because identity is GitHub's permanent repository id, not the name.

To cut Drake off immediately: suspend the installation in GitHub. Any
cached installation token expires within the hour and cannot be renewed
without the app JWT.

## Verifying that nothing leaks

- `GET /v1/integrations/github/status` and every other endpoint return
  readiness flags and observed metadata only. If you ever see a key,
  token, or secret in an API response, treat it as an incident.
- Audit records for GitHub actions carry reason codes and counts, never
  payloads or headers.
- The webhook delivery table stores a bounded envelope and a digest —
  never the raw payload.
- To spot-check redaction locally, run a reconcile with the API log level
  at debug and grep the output for `ghs_`, `BEGIN`, and `Authorization`.

## Local development

No real credentials are needed. Tests generate an RSA key at runtime and
drive a fake GitHub through an injected transport; the E2E suite runs a
local fake GitHub server. Never point a local Drake at the real
organization.

## The Datalake security gate

`Duosis-Developer-Team/Datalake-Platform-GUI` is blocked in code. While
the gate is open Drake performs **no** credential read, no installation
access, no reconciliation and no API call against it — the block is
enforced before any network path exists.

Closing the gate requires, in order: authorized operator review,
credential rotation where the exposed values warrant it, git-history
containment, `.gitignore` remediation, and a safe `.env.example`
contract. Only then may the gate be removed from
`apps/api/src/drake_api/github_app/catalog.py` — a reviewed code change,
never a runtime toggle.

## Production activation

Turning this on against the real organization is a separate, explicit
decision. It requires CTO/operator approval, and it inherits the standing
Sprint 3 requirement that production ingress route `/v1` directly to the
API. Sprint 5A ships the capability; it does not activate it.


## Sprint 5B: repository onboarding

### App permission change

Sprint 5B adds **Contents: read** to the App's requested permissions. It
is needed to resolve the default branch to a commit SHA and to read the
allowlisted metadata files — above all `.drake/project.yaml` — at that
SHA. No write permission is added, and none is used.

Changing a GitHub App's permissions requires each installation to accept
the new set. Until an installation accepts, `contents` will not appear in
the token's granted permissions and Drake will refuse to scan, naming the
missing permission rather than proceeding with a partial read.

### Selected repositories, not "all"

Install the App against **selected repositories**. It is the difference
between "Drake can read the four repositories we chose" and "Drake can
read everything the organization owns", and it is the control that keeps
Datalake-Platform-GUI out of reach at the provider level as well as
behind Drake's own gate.

### What the scanner will and will not do

It reads a fixed allowlist — the manifest, README, `package.json`,
`pyproject.toml`, bounded requirements files, Dockerfile and Compose
metadata, bounded `.github/workflows` and Kubernetes/Helm/Kustomize
metadata — through the Contents API, at one commit, under these budgets:

| Budget | Value |
|---|---|
| Files inspected | 60 |
| Bytes per file | 256 KiB |
| Total decoded bytes | 1 MiB |
| Provider calls | 80 |
| Wall clock | 15 s |
| Directory depth | 2 |
| Manifest size | 128 KiB |

Nothing is cloned, downloaded as an archive, or executed. A budget being
exhausted is reported as an incomplete scan, and an incomplete scan is
never importable.

### Where onboarding happens now (Sprint 12A.2a)

**Use `/onboarding`.** The panel that used to open inside the repository
card on `/integrations/github` is gone, and the five endpoints behind it
answer `410 Gone` with `legacy_onboarding_retired`.

It was retired because it wrote catalog rows with no plan, no approval, no
plan digest and no apply receipt — every guarantee Sprints 11 and 12A.1
were built to provide, optional. The repository card now links to the
reviewed flow instead, and the link appears only for an operator who holds
`onboarding.manage`; managing the integration is a different permission
from onboarding a project.

The full operator walkthrough is in
[PROJECT_ONBOARDING.md](PROJECT_ONBOARDING.md).

### The GitOps workflow

If the repository already contains a valid `.drake/project.yaml`, analyse
it in a session, review the plan, approve it and apply it. The imported
project appears in the catalog; nothing is deployed.

If it does not, download a starting point from
`GET /v1/onboarding/sessions/{id}/manifest-draft` — the "Download manifest
draft" action on the session screen. It is generated from that session's
stored analysis, so Drake fills in only the repository block, because that
is all it can see. Ownership, cluster, namespace, criticality, tenant model
and metrics profile are left as `REPLACE_ME` — a plausible guess would
invite being committed unread.

**Commit the file to the repository yourself, then analyse again.** Drake
cannot commit it for you: `Contents: write` is not requested, and no
pull-request provider exists.

The only implementation is a recording test double used by the test suite.
A production process may not hold one — it would report a pull request that
does not exist — so `github_gitops_pr_enabled` and `gitops_worker_enabled`
are refused outside local/test and the process fails to start with either
set. Sprint 12B builds the real provider; switching it on is a separate
decision after that. And a draft edited in the browser is never importable regardless:
ADR-0007 makes the repository the source of intent, and an import that
accepted a browser copy would record intent the repository never
expressed.

### First target

The first real repository is `Duosis-Developer-Team/Hermes`.

`Duosis-Developer-Team/Datalake-Platform-GUI` remains **excluded**. Its
manual `.env` security gate is still open, and both the scan and the
import refuse it before any token is minted or any provider call is made.
Closing that gate is an authorized-operator process, unchanged by this
sprint.

### Still requiring operator action

- Registering and installing the real GitHub App, and accepting the new
  Contents: read permission.
- Production activation, which still inherits the unresolved Sprint 3
  production ingress `/v1` requirement.
- Closing the Datalake `.env` security gate, if and when that is decided.

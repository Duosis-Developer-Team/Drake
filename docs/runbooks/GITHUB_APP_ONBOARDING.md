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

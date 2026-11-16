# ADR-0030: E-mail notifications through Microsoft Graph

**Status:** Accepted
**Extends:** ADR-0024 (notification routing and reliable delivery)

## Context

ADR-0024 gave incidents two destinations: the in-app inbox and operator
webhooks. The people who respond to an incident here read e-mail, and the
organisation's mail is Microsoft 365. The in-house services that already send
mail do it through Microsoft Graph with an Azure app registration (the
client-credentials flow) and a sender mailbox, not through SMTP.

## Decision

### A third destination type, `email`, keyed like a webhook

An e-mail destination row holds an opaque **key** into an operator-owned
registry (`notification_emails`: key → recipients), exactly like a webhook
row holds a key into `notification_webhooks`. No address is stored in the
database, and no endpoint accepts one: a policy can only mail a list the
operator configured. The API shows a key's display name and how many people
it reaches, not the addresses.

Webhook and e-mail keys share one namespace. Settings refuses a key that
appears in both registries, so a delivery row's key names exactly one
destination and the worker never has to guess.

### The webhook outbox carries e-mail too

The planner freezes the same small, server-composed payload for an e-mail
destination as for a webhook and writes it to `webhook_deliveries`. The
delivery worker resolves the key and hands the row to `send_email` instead of
`send_webhook`. Retries, the bounded budget, dead letters, suppression, the
attempt audit and at-least-once delivery are therefore the ADR-0024 ones,
unchanged and re-tested, not a second implementation. The channel is part of
the idempotency key.

Migration 0025 only widens two check constraints to admit the type. No table
or column is added and no row is rewritten.

### One sender, through Microsoft Graph

`notification_email_sender` names the tenant, the client, the sending mailbox
and a **file reference** to the client secret, mounted from a Kubernetes
Secret. The secret is read when a token is requested and goes only to the
Microsoft login endpoint. The token is cached in memory until shortly before
it expires. Outside local/test both Microsoft endpoints must be https.

The message is plain text, composed from the frozen payload: subject
`[Drake][SEVERITY] Incident opened: <title>`, then the service, the workload,
the time and an absolute link built from the public origin. Every field is
collapsed to one line, so no title can add a header. Graph has no idempotency
key, so the delivery key travels as an `X-Drake-Idempotency-Key` message
header.

Classification: 2xx means delivered. 429, 5xx, timeouts and connection
errors are retryable, and so is a 401 from Graph (the cached token is dropped
and replaced on the next try). Other 4xx (403 for a missing `Mail.Send` or a
mailbox the app may not send as, 400, 404) and a refused token request are
terminal. An unreadable secret is retryable, so a rotation in progress does
not lose a message. Recipients configured without a sender are suppressed:
never tried, rather than tried and failed.

### Off until an operator turns it on

The chart states the planner and delivery switches in both states, off by
default. It renders the sender and the registry only when they are set, and
refuses to render a sender without its tenant, its client and its Secret.
Nothing is mailed until all three of these hold: a sender is configured, a
destination is attached to a policy, and both switches are on.

## Consequences

- Turning e-mail on for a release takes four things: the Azure app
  (`Mail.Send` application permission, ideally limited to one mailbox by an
  application access policy), the Secret, the values, and a policy. None of
  them can be done from the API alone.
- A crash between Graph accepting a message and Drake recording it sends that
  message twice. This is the at-least-once contract webhooks already have.
- Slack, Teams or another channel would be a fourth type in the same shape:
  a keyed registry, a transport, and the same outbox.

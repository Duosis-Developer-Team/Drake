# Backlog

Known issues that are understood, not urgent, and deliberately not being
fixed yet. Each entry records what it is, why it was deferred, and what
would make it worth doing — so that deferring stays a decision rather than
a thing that quietly happened.

---

## CI: the API image entrypoint smoke has an unretried registry dependency

**Recorded** 2026-08-11 (Sprint 13F.3), by CTO direction, after the
post-merge `main` failure of run `31442626837`.

`scripts/api_image_entrypoint_smoke.sh` runs

```
docker build -q --platform linux/amd64 -f apps/api/Dockerfile -t drake-api:entrypoint-smoke .
```

whose first instruction pulls a digest-pinned `python:3.13-slim-bookworm`
base image straight from Docker Hub. There is no retry, no fallback and no
registry cache.

**What happened.** Run `31442626837` failed on `main` with:

```
ERROR: failed to build: failed to solve: DeadlineExceeded:
  failed to resolve source metadata for docker.io/library/python@sha256:67a1e1f2…:
  Head "https://registry-1.docker.io/v2/library/python/manifests/sha256:67a1e1f2…":
  dial tcp 52.201.63.90:443: i/o timeout
```

A 60-second TCP timeout to Docker Hub, before any repository code ran. The
byte-identical tree (`16e8d31db736bc76…`) had passed the same step on the
PR run 20 minutes earlier, and a rerun of only the failed job went green
with no code change.

**Why this is the exposed surface.** It is the only unretried external
network dependency in the `python` job. Every other step works from
already-fetched dependencies. So this class of failure will recur, and each
recurrence costs a rerun and looks — at first glance — like a real failure
on `main`.

**Deferred deliberately.** The CTO's disposition for Sprint 13F.3 was to
record this and change nothing: a rerun must not become the standard way to
hide a failure, and a retry wrapper added reflexively is a step toward
exactly that. The fix should be chosen on its merits, not while recovering
from an incident.

**Options, when it is picked up.**

- A bounded retry with backoff around the `docker build` invocation only —
  smallest change, but it makes a genuinely broken build slower to fail and
  must not swallow non-network errors.
- A registry pull-through cache or a mirror for the base image — removes
  the dependency rather than tolerating it, at the cost of infrastructure
  to run.
- `docker/build-push-action` with GitHub Actions layer caching — caches the
  base layer between runs; changes how the smoke builds, so the smoke's
  fidelity to the production build needs re-checking.

**Worth doing when** this recurs often enough to be measured, or when any
other CI step gains a registry dependency. Until then it is one rerun,
diagnosable from the log in under a minute, and the log signature is
recorded above so the next person does not have to re-derive it.

---

## ~~Onboarding: `owner_team_unknown` is a reason code nothing emits~~ — RESOLVED

**Recorded** 2026-08-11 (Sprint 13F.3), found while preparing the Fikir
Sepeti admission pack. **Resolved the same sprint**, by CTO direction, in
the 13F.3 review fixes. Kept here because the reasoning is worth not
re-deriving.

`REASON_TEXT` defined `owner_team_unknown` and nothing emitted it, so it
read as a guarantee that an unrecognised owning team would be refused —
and two manifests plus three documents described that guarantee in prose.
None of it was true.

**The underlying behaviour was correct and stays.** Drake has no
independent owner-team catalog; a team key is bounded metadata that grants
no permission, and if unknown teams blocked, the first project any team
owns would be permanently unonboardable. Authority comes from RBAC grants,
which no manifest can reach.

**What was actually broken was narrower and worse.** Every owner planned
`no_change` / `applied_with_parent`, which is true only while the project is
created in the same transaction. On an existing project nothing wrote
`project_owners` at all, so adding an owner planned "nothing to do" and did
nothing — plan and apply agreeing with each other, both wrong.

Resolution: dead reason code deleted; per-project ownership added to the
snapshot keyed like `uq_project_owner`; three cases distinguished (created
with a new project / already recorded / a real `create` on an existing one);
an apply handler added for the third; false prose corrected in
`logislot.project.yaml`, `project-epsilon.yaml`, `LOGISLOT.md`, `HERMES.md`
and `PROJECT_ONBOARDING.md`; twelve regression tests in
`test_owner_team_apply_integration.py`.

**Still deliberately not done:** Drake does not refuse an unevidenced owner.
Confirming ownership is an operator decision on the manifest. If that should
ever change it is a product decision about onboarding policy, not a bug
fix — and it would need a real answer for the first-project-per-team case.

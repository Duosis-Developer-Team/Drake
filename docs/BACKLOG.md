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

## Onboarding: `owner_team_unknown` is a reason code nothing emits

**Recorded** 2026-08-11 (Sprint 13F.3), found while preparing the Fikir
Sepeti admission pack.

`ONBOARDING_REASONS` defines:

```
"owner_team_unknown": "The manifest references an owning team Drake does not have."
```

Nothing emits it. `build_plan` plans every owner team as `no_change` /
`applied_with_parent` whether or not the catalog knows the key, and
`_apply_project` creates the `project_owners` row from whatever the
manifest named.

**This is deliberate**, and the reasoning in `model.py` is sound: an owner
team key is a bounded label that grants no permission, and if unknown teams
blocked, the first project any team owns would be permanently
unonboardable. Authority comes from RBAC grants, which no manifest can
touch.

**But it was documented as if it did block.** Comments in
`logislot.project.yaml` and `project-epsilon.yaml` claimed an unknown team
"resolves to an `unmapped` plan item". Both were wrong and are corrected;
`test_fikir_sepeti_admission.py` now pins the real behaviour.

**The open question is a product decision, not a bug fix:** should naming
an unevidenced owner be blocked, warned about, or left alone? Today it is
left alone, ownership is an operator gate on the manifest rather than a
planner gate, and Fikir Sepeti's admission is held on exactly that gate —
see `docs/onboarding/FIKIR_SEPETI.md`. Either wire the reason code up or
delete it; a defined-and-never-emitted reason code reads as a guarantee
that does not exist.

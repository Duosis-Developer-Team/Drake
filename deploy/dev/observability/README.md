# Drake dev observability package

Pinned [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
wrapper for the future dev cluster. **Sprint 3 renders and policy-checks
this package in CI only — nothing installs it.** Applying it to a real
cluster is a separate, operator-approved step.

## Contents

| File | Purpose |
|---|---|
| `Chart.yaml` | Wrapper chart pinning `kube-prometheus-stack` to an exact version |
| `Chart.lock` | Resolved dependency digest (reproducible `helm dependency build`) |
| `values.yaml` | Drake's dev values (see below) |
| `validate.sh` | `helm lint` + `helm template` + rendered-manifest policy checks |

## Posture

- Prometheus Operator, Prometheus (1 replica, 15d retention, explicit PVC),
  Alertmanager, kube-state-metrics, node-exporter **enabled**; Grafana
  **disabled** (Drake is the consumer).
- External labels: `cluster`, `site`, `environment`, plus the operator's
  `prometheus_replica` replica label.
- Explicit ServiceMonitor/PodMonitor selectors — nothing is adopted
  implicitly.
- No Ingress, no NodePort, no LoadBalancer, no embedded credentials, no
  Thanos object-store secret. Chart security contexts and probes are kept.
- This is a **dev** posture: single replica, no HA claims.

## Validating locally

```bash
bash deploy/dev/observability/validate.sh
```

Requires `helm` and network access to the prometheus-community chart
repository (the downloaded chart archive is not committed; `Chart.lock`
pins its digest).

## Known limitation

The upstream chart pins its images by **tag**, not digest, for several
subcomponents. This is inherited and reported honestly; the real-deployment
gate (operator approval) is the compensating control until images are
mirrored/pinned during actual onboarding.

## Deploying (NOT part of Sprint 3)

Deployment requires explicit operator approval and happens outside this
repository's automation:

```bash
# operator-only, after review:
helm dependency build deploy/dev/observability
helm upgrade --install drake-dev-observability deploy/dev/observability \
  --namespace drake-observability --create-namespace
```

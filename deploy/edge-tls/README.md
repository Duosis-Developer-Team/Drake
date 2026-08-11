# Edge TLS — a real certificate without a purchased domain

The production edge served the ingress controller's default self-signed
certificate. That was recorded as "browsers will warn", which understated
it: **GitHub refuses to deliver a webhook to an untrusted certificate**,
and a webhook delivery is the only thing that creates an installation row
and enqueues repository reconciliation
(`queue_installation_reconciliation` is called from exactly two places,
both inside webhook delivery processing). So the self-signed certificate
was not a cosmetic limitation — it was what kept the production catalog
empty.

No domain was purchased to fix it, and none is needed yet.

## Why node2's name

`sslip.io` names are real public DNS, so Let's Encrypt will issue for
them. HTTP-01 has to be answered on **port 80 of the address the name
resolves to**, and the two nodes differ:

| | node1 `84.247.180.172` | node2 `84.247.180.173` |
|---|---|---|
| `:80` | iptables REDIRECT → NodePort 30880 | shared `ingress-nginx`, hostNetwork |
| `:443` | iptables REDIRECT → NodePort 30443 | shared `ingress-nginx`, hostNetwork |
| controller behind it | `ingress-nginx-test`, `--watch-namespace=hermes-test` | `ingress-nginx`, watches every namespace |

Solving on node1 would mean writing a solver Ingress into `hermes-test`
— another team's namespace, for our certificate. node2's controller
watches all namespaces, so the solver Ingress cert-manager creates lives
in `drake-prod` and nothing of Hermes' is touched.

A certificate is bound to the **name**, not the port, so serving it on
NodePort 30773 is fine: `https://drake-84-247-180-173.sslip.io:30773`
validates in a browser and to GitHub.

## Applying

```
kubectl apply -f deploy/edge-tls/cluster-issuer.yaml
kubectl apply -f deploy/edge-tls/certificate.yaml
```

cert-manager (`jetstack/cert-manager`, installed in its own namespace)
renews automatically; nothing here needs a calendar reminder. The issued
Secret is `drake-prod/drake-edge-tls`, which
`values-drake-prod.yaml` names in `ingress.tls.secretName`.

## When a real domain arrives

Change the `dnsNames` entry here and `ingress.host` +
`publicOrigin` in `values-drake-prod.yaml`, point the domain's A record
at a node, and re-apply. Nothing else in the chart is address-aware.

## What is not in this directory

The ACME account key and the issued certificate. Both are created by
cert-manager inside the cluster and neither is ever written to Git.

{{/*
Fail-closed production contract (ADR-0021).

Every check below refuses to RENDER rather than producing a manifest that
would install and then behave differently than intended. A chart that
renders something plausible from a missing value is how a production edge
ends up without TLS, or with an Ingress matching a host nobody owns.
*/}}

{{- define "drake.name" -}}drake{{- end -}}

{{- define "drake.production" -}}
{{- eq .Values.deploymentMode "production" -}}
{{- end -}}

{{- define "drake.labels" -}}
app.kubernetes.io/name: {{ include "drake.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: drake
{{- end -}}

{{/* Every resource carries its namespace explicitly. Under GitOps the
     rendered YAML is the reviewed artifact, and "which namespace does this
     land in" must be answerable from the file, not from the command line
     someone happened to run. */}}
{{- define "drake.namespace" -}}
{{- .Values.namespaceOverride | default .Release.Namespace -}}
{{- end -}}

{{/* imagePullSecrets, or nothing at all.

     Emitting `imagePullSecrets:` with an empty list is not harmless: it is
     a valid field with a meaningless value, and it hides the difference
     between "no pull secret is needed" and "someone forgot to set one".
     This define produces no output when the list is empty, so the pod spec
     simply does not carry the key. */}}
{{- define "drake.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Which edge mode is active. */}}
{{- define "drake.cloudflaredMode" -}}
{{- eq (.Values.edge).mode "cloudflared" -}}
{{- end -}}

{{- define "drake.ingressMode" -}}
{{- eq (.Values.edge).mode "ingress" -}}
{{- end -}}

{{/* Exactly one edge mode, and every input that mode needs. */}}
{{- define "drake.validateEdge" -}}
{{- $mode := (.Values.edge).mode | default "" -}}
{{- if not (has $mode (list "ingress" "cloudflared")) -}}
  {{- fail (printf "edge.mode must be \"ingress\" or \"cloudflared\", got %q" $mode) -}}
{{- end -}}
{{- if eq $mode "cloudflared" -}}
  {{- if .Values.ingress.enabled -}}
    {{- fail "edge.mode=cloudflared serves Drake through an outbound Tunnel; ingress.enabled must be false so no inbound route is created" -}}
  {{- end -}}
  {{- if eq (include "drake.production" .) "true" -}}
    {{- if not .Values.cloudflared.tunnelSecretName -}}
      {{- fail "cloudflared.tunnelSecretName is required: the connector needs a Secret holding tunnel-id and credentials.json, created out of band" -}}
    {{- end -}}
    {{- if not .Values.cloudflared.image.digest -}}
      {{- fail "cloudflared.image.digest is required in production" -}}
    {{- end -}}
    {{- if not (hasPrefix "sha256:" .Values.cloudflared.image.digest) -}}
      {{- fail "cloudflared.image.digest must be a sha256 digest" -}}
    {{- end -}}
    {{- if lt (int .Values.cloudflared.replicas) 2 -}}
      {{- fail "cloudflared.replicas must be at least 2: a single connector makes Drake unavailable whenever its node is drained" -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/* The public host, taken from the ingress and cross-checked against the
     public origin. Two sources of truth for one hostname is how a redirect
     ends up pointing somewhere the certificate does not cover. */}}
{{- define "drake.validateHost" -}}
{{- $origin := .Values.publicOrigin | default "" -}}
{{- $host := .Values.ingress.host | default "" -}}
{{- if eq (include "drake.production" .) "true" -}}
  {{- if not $origin -}}
    {{- fail "publicOrigin is required in production" -}}
  {{- end -}}
  {{- if not (hasPrefix "https://" $origin) -}}
    {{- fail "publicOrigin must use https in production" -}}
  {{- end -}}
  {{- $originHost := trimPrefix "https://" $origin | trimSuffix "/" -}}
  {{- if eq (include "drake.ingressMode" .) "true" -}}
    {{- if not $host -}}
      {{- fail "ingress.host is required in production" -}}
    {{- end -}}
    {{- if ne $originHost $host -}}
      {{- fail (printf "publicOrigin host (%s) and ingress.host (%s) must be the same origin" $originHost $host) -}}
    {{- end -}}
  {{- end -}}
  {{- $host = $originHost -}}
  {{- if contains ":" $host -}}
    {{- fail (printf "publicOrigin %q carries a port; a Kubernetes Ingress host and a Tunnel hostname are both port-less. Publish Drake on 443." $origin) -}}
  {{- end -}}
  {{- if contains "*" $host -}}
    {{- fail "ingress.host must be an exact host, not a wildcard" -}}
  {{- end -}}
  {{- if or (contains "REPLACE_ME" $host) (contains "<" $host) -}}
    {{- fail "ingress.host is still a placeholder" -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/* The host itself, for templates that need to print it. Validation is a
     separate define so including it cannot emit a stray line into a
     manifest. */}}
{{- define "drake.publicHost" -}}
{{- include "drake.validateHost" . -}}
{{- if eq (include "drake.production" .) "true" -}}
{{- trimPrefix "https://" (.Values.publicOrigin | default "") | trimSuffix "/" -}}
{{- else -}}
{{- .Values.ingress.host | default "" -}}
{{- end -}}
{{- end -}}

{{/* Production requires an Ingress with a class and TLS. */}}
{{- define "drake.validateIngress" -}}
{{- if and (eq (include "drake.production" .) "true") (eq (include "drake.ingressMode" .) "true") -}}
  {{- if not .Values.ingress.enabled -}}
    {{- fail "ingress.enabled must be true in production: Drake is served from one public origin" -}}
  {{- end -}}
  {{- if not .Values.ingress.className -}}
    {{- fail "ingress.className is required in production" -}}
  {{- end -}}
  {{- if not .Values.ingress.tls.enabled -}}
    {{- fail "ingress.tls.enabled must be true in production: public traffic requires HTTPS" -}}
  {{- end -}}
  {{- if not .Values.ingress.tls.secretName -}}
    {{- fail "ingress.tls.secretName is required in production" -}}
  {{- end -}}
  {{- range $key, $value := .Values.ingress.annotations -}}
    {{- if or (contains "rewrite-target" $key) (contains "configuration-snippet" $key) (contains "server-snippet" $key) -}}
      {{- fail (printf "annotation %s is not allowed: the API owns /v1 and the path must reach it unmodified" $key) -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/* An image reference that cannot drift. */}}
{{- define "drake.image" -}}
{{- $ctx := .ctx -}}
{{- $image := .image -}}
{{- $name := .name -}}
{{- if eq (include "drake.production" $ctx) "true" -}}
  {{- if not $image.digest -}}
    {{- fail (printf "%s image must be digest-pinned in production (image.digest)" $name) -}}
  {{- end -}}
  {{- if not (hasPrefix "sha256:" $image.digest) -}}
    {{- fail (printf "%s image.digest must be a sha256 digest" $name) -}}
  {{- end -}}
  {{- if $image.tag -}}
    {{- fail (printf "%s must not set image.tag in production; a digest is the only immutable reference" $name) -}}
  {{- end -}}
{{- end -}}
{{- if $image.digest -}}
{{ $image.repository }}@{{ $image.digest }}
{{- else if $image.tag -}}
  {{- if eq $image.tag "latest" -}}
    {{- fail (printf "%s image tag 'latest' is never deployable" $name) -}}
  {{- end -}}
{{ $image.repository }}:{{ $image.tag }}
{{- else -}}
  {{- fail (printf "%s image needs a digest (production) or a tag (disposable clusters)" $name) -}}
{{- end -}}
{{- end -}}

{{/* Secret REFERENCES only. A value here would end up in `helm get values`,
     in CI logs, and in whatever copy of the values file someone emails. */}}
{{- define "drake.validateSecrets" -}}
{{- if eq (include "drake.production" .) "true" -}}
  {{- if not .Values.api.existingSecret -}}
    {{- fail "api.existingSecret is required in production: application secrets are referenced, never inlined" -}}
  {{- end -}}
  {{- if .Values.github.enabled -}}
    {{- if not .Values.github.existingSecret -}}
      {{- fail "github.existingSecret is required when the GitHub App integration is enabled" -}}
    {{- end -}}
    {{- if not (or .Values.github.appId .Values.github.clientId) -}}
      {{- fail "github.appId or github.clientId is required when the GitHub App integration is enabled" -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/* Default-deny stays; the ingress controller is admitted explicitly. */}}
{{- define "drake.validateNetworkPolicy" -}}
{{- if eq (include "drake.production" .) "true" -}}
  {{- if not .Values.networkPolicy.enabled -}}
    {{- fail "networkPolicy.enabled must be true in production" -}}
  {{- end -}}
  {{- if and (eq (include "drake.ingressMode" .) "true") (not .Values.networkPolicy.ingressControllerNamespaceSelector) -}}
    {{- fail "networkPolicy.ingressControllerNamespaceSelector is required: default-deny would otherwise block the route just configured" -}}
  {{- end -}}
  {{- if not .Values.networkPolicy.databaseCIDR -}}
    {{- fail "networkPolicy.databaseCIDR is required in production" -}}
  {{- end -}}
  {{- if not .Values.networkPolicy.redisCIDR -}}
    {{- fail "networkPolicy.redisCIDR is required in production" -}}
  {{- end -}}
  {{- range $cidr := list .Values.networkPolicy.databaseCIDR .Values.networkPolicy.redisCIDR -}}
    {{- if or (eq $cidr "0.0.0.0/0") (eq $cidr "::/0") -}}
      {{- fail "egress CIDRs must be specific; 0.0.0.0/0 defeats the policy" -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/* Public Services stay internal: the Ingress is the only front door. */}}
{{- define "drake.serviceType" -}}
{{- if eq (include "drake.production" .) "true" -}}ClusterIP{{- else -}}ClusterIP{{- end -}}
{{- end -}}

{{- define "drake.validateAll" -}}
{{- include "drake.validateEdge" . -}}
{{- include "drake.validateIngress" . -}}
{{- include "drake.validateSecrets" . -}}
{{- include "drake.validateNetworkPolicy" . -}}
{{- include "drake.validateHost" . -}}
{{- end -}}

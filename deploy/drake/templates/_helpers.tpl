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

{{/* Telemetry connectors as the API's settings expect them.

     The chart speaks camelCase and the settings model speaks snake_case;
     translating here rather than asking an operator to write `allow_private`
     in a values file keeps one spelling per audience. Emitted as JSON
     because pydantic-settings parses a complex field from one env var, and
     the values are addresses — never credentials, which is why this may be
     an env var at all. */}}
{{- define "drake.telemetryConnectors" -}}
{{- $out := dict -}}
{{- range $name, $connector := (.Values.telemetry).connectors }}
{{- $_ := set $out $name (dict
      "url" (required (printf "telemetry.connectors.%s.url is required" $name) $connector.url)
      "allow_private" (default false $connector.allowPrivate)
      "allow_plaintext" (default false $connector.allowPlaintext)) -}}
{{- end }}
{{- toJson $out -}}
{{- end -}}

{{/* Which edge mode is active. */}}
{{- define "drake.internalMode" -}}
{{- eq (.Values.edge).mode "internal" -}}
{{- end -}}

{{- define "drake.ingressMode" -}}
{{- eq (.Values.edge).mode "ingress" -}}
{{- end -}}

{{/* Exactly one edge mode, and every input that mode needs. */}}
{{- define "drake.validateEdge" -}}
{{- $mode := (.Values.edge).mode | default "" -}}
{{- if not (has $mode (list "internal" "ingress")) -}}
  {{- fail (printf "edge.mode must be \"internal\" or \"ingress\", got %q" $mode) -}}
{{- end -}}
{{- if eq $mode "internal" -}}
  {{- if .Values.ingress.enabled -}}
    {{- fail "edge.mode=internal publishes no public route; set edge.mode=ingress to enable one" -}}
  {{- end -}}
{{- end -}}
{{- if and (eq $mode "ingress") (.Values.edge.dedicatedController).enabled -}}
  {{- $c := .Values.edge.dedicatedController -}}
  {{- if eq (include "drake.production" .) "true" -}}
    {{- if not $c.image.digest -}}
      {{- fail "edge.dedicatedController.image.digest is required in production" -}}
    {{- end -}}
    {{- if not (hasPrefix "sha256:" $c.image.digest) -}}
      {{- fail "edge.dedicatedController.image.digest must be a sha256 digest" -}}
    {{- end -}}
  {{- end -}}
  {{- if not $c.controllerClass -}}
    {{- fail "edge.dedicatedController.controllerClass is required: two controllers sharing a class would each serve the other's Ingresses" -}}
  {{- end -}}
  {{- if not $c.ingressClassName -}}
    {{- fail "edge.dedicatedController.ingressClassName is required" -}}
  {{- end -}}
  {{- if or (lt (int $c.httpsNodePort) 30000) (gt (int $c.httpsNodePort) 32767) -}}
    {{- fail (printf "edge.dedicatedController.httpsNodePort must be inside the NodePort range, got %v" $c.httpsNodePort) -}}
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
    {{- if ne ((splitList ":" $originHost) | first) $host -}}
      {{- fail (printf "publicOrigin host (%s) and ingress.host (%s) must be the same hostname" ((splitList ":" $originHost) | first) $host) -}}
    {{- end -}}
  {{- end -}}
  {{/* A public origin may carry a port — the edge is a NodePort, not 443.
       The Ingress `host:` field cannot, so the port is split off here and
       the hostname alone is what the Ingress matches on. */}}
  {{- $host = (splitList ":" $originHost) | first -}}
  {{- if not $host -}}
    {{- fail (printf "publicOrigin %q has no hostname" $origin) -}}
  {{- end -}}
  {{- if contains "*" $host -}}
    {{- fail "ingress.host must be an exact host, not a wildcard" -}}
  {{- end -}}
  {{/* A bare IP is refused here as well as in the API. Cookies are not
       scoped by port, so an origin identified only by address shares its
       cookie jar with every other service on that address. */}}
  {{- if regexMatch "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$" $host -}}
    {{- fail (printf "publicOrigin host %q is a bare IP address; use a hostname" $host) -}}
  {{- end -}}
  {{- if or (contains "REPLACE_ME" $host) (contains "<" $host) -}}
    {{- fail "ingress.host is still a placeholder" -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/* The host itself, for templates that need to print it. Validation is a
     separate define so including it cannot emit a stray line into a
     manifest. */}}
{{/* The hostname the Ingress matches on: no scheme, and no port. */}}
{{- define "drake.publicHost" -}}
{{- include "drake.validateHost" . -}}
{{- if eq (include "drake.production" .) "true" -}}
{{- $origin := trimPrefix "https://" (.Values.publicOrigin | default "") | trimSuffix "/" -}}
{{- (splitList ":" $origin) | first -}}
{{- else -}}
{{- .Values.ingress.host | default "" -}}
{{- end -}}
{{- end -}}

{{/* The full origin, port included: what the application must believe it
     is reachable at, and what every redirect and cookie derives from. */}}
{{- define "drake.publicOrigin" -}}
{{- include "drake.validateHost" . -}}
{{- .Values.publicOrigin | trimSuffix "/" -}}
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
  {{- if not (has .Values.ingress.tls.mode (list "secret" "controller-default")) -}}
    {{- fail "ingress.tls.mode must be \"secret\" or \"controller-default\"" -}}
  {{- end -}}
  {{- if and (eq .Values.ingress.tls.mode "secret") (not .Values.ingress.tls.secretName) -}}
    {{- fail "ingress.tls.secretName is required when tls.mode=secret" -}}
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

{{/* DNS, narrowed to the resolver pods themselves.

     Both selectors sit in ONE peer entry so they intersect rather than
     union: the named pods, in the named namespace. Two separate entries
     would permit every pod in that namespace AND those pods everywhere. */}}
{{- define "drake.dnsEgress" -}}
{{- $dns := .Values.networkPolicy.dns -}}
- to:
    - namespaceSelector:
        matchLabels:
          {{- toYaml $dns.namespaceSelector.matchLabels | nindent 10 }}
      podSelector:
        matchLabels:
          {{- toYaml $dns.podSelector.matchLabels | nindent 10 }}
  ports:
    - protocol: UDP
      port: {{ $dns.port }}
    - protocol: TCP
      port: {{ $dns.port }}
{{- end -}}

{{/* One in-cluster datastore peer, selected by label.

     No namespaceSelector is emitted, which is what confines the peer to
     Drake's own namespace — the only place this chart can also create the
     matching ingress policy. */}}
{{- define "drake.datastoreEgress" -}}
{{- $peer := .peer -}}
- to:
    - podSelector:
        matchLabels:
          {{- toYaml $peer.podSelector.matchLabels | nindent 10 }}
  ports:
    - protocol: TCP
      port: {{ $peer.port }}
{{- end -}}

{{/* Default-deny stays; the ingress controller is admitted explicitly. */}}
{{- define "drake.validateNetworkPolicy" -}}
{{- if eq (include "drake.production" .) "true" -}}
  {{- if not .Values.networkPolicy.enabled -}}
    {{- fail "networkPolicy.enabled must be true in production" -}}
  {{- end -}}
  {{/* Only a controller in ANOTHER namespace needs a namespaceSelector.
       Drake's own controller is a pod in this namespace and is admitted by
       label, which is both narrower and impossible to point at the wrong
       place. */}}
  {{- if and (eq (include "drake.ingressMode" .) "true")
             (not .Values.edge.dedicatedController.enabled)
             (not .Values.networkPolicy.ingressControllerNamespaceSelector) -}}
    {{- fail "networkPolicy.ingressControllerNamespaceSelector is required: default-deny would otherwise block the route just configured" -}}
  {{- end -}}
  {{- range $name := list "database" "redis" -}}
    {{- $peer := index $.Values.networkPolicy $name -}}
    {{- if not $peer -}}
      {{- fail (printf "networkPolicy.%s is required in production" $name) -}}
    {{- end -}}
    {{- if not (($peer.podSelector).matchLabels) -}}
      {{- fail (printf "networkPolicy.%s.podSelector.matchLabels is required: an empty selector would match every pod in the namespace" $name) -}}
    {{- end -}}
    {{- if not $peer.port -}}
      {{- fail (printf "networkPolicy.%s.port is required in production" $name) -}}
    {{- end -}}
  {{- end -}}
  {{- range $name := list "database" "redis" -}}
    {{- $peer := index $.Values.networkPolicy $name -}}
    {{/* hasKey, not truthiness: an empty `namespaceSelector: {}` left in a
         stale overlay is exactly the case that must not pass silently. */}}
    {{- if hasKey $peer "namespaceSelector" -}}
      {{- fail (printf "networkPolicy.%s.namespaceSelector is not supported and must be removed: a NetworkPolicy governs only its own namespace, so the datastore must live in Drake's namespace" $name) -}}
    {{- end -}}
  {{- end -}}
  {{- $dns := .Values.networkPolicy.dns -}}
  {{- if not (($dns.namespaceSelector).matchLabels) -}}
    {{- fail "networkPolicy.dns.namespaceSelector.matchLabels is required: an empty selector permits port 53 on every pod in the cluster" -}}
  {{- end -}}
  {{- if not (($dns.podSelector).matchLabels) -}}
    {{- fail "networkPolicy.dns.podSelector.matchLabels is required: an empty selector permits port 53 on every pod in the DNS namespace" -}}
  {{- end -}}
  {{- if not $dns.port -}}
    {{- fail "networkPolicy.dns.port is required in production" -}}
  {{- end -}}
  {{- if ne (int $dns.port) 53 -}}
    {{- fail (printf "networkPolicy.dns.port must be 53; %v would open a different port under the name \"DNS\"" $dns.port) -}}
  {{- end -}}
  {{- if eq (toYaml .Values.networkPolicy.database.podSelector.matchLabels) (toYaml .Values.networkPolicy.redis.podSelector.matchLabels) -}}
    {{- fail "networkPolicy.database and networkPolicy.redis must select different pods; identical selectors grant each the other's access" -}}
  {{- end -}}
  {{- range $entry := .Values.networkPolicy.apiExternalEgress -}}
    {{- if not $entry.cidr -}}
      {{- fail "every networkPolicy.apiExternalEgress entry needs a cidr" -}}
    {{- end -}}
    {{- if or (eq $entry.cidr "0.0.0.0/0") (eq $entry.cidr "::/0") -}}
      {{- fail "networkPolicy.apiExternalEgress entries must be specific; 0.0.0.0/0 defeats the policy" -}}
    {{- end -}}
    {{- if not $entry.port -}}
      {{- fail "every networkPolicy.apiExternalEgress entry needs a port" -}}
    {{- end -}}
    {{- if ne (int $entry.port) 443 -}}
      {{- fail (printf "networkPolicy.apiExternalEgress is for outbound HTTPS; port %v is not 443" $entry.port) -}}
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
{{- include "drake.validateGitOps" . -}}
{{- end -}}

{{/*
Repository writes are refused unless the whole decision is made.

Rendered rather than left to the API's own startup validation as well: a
chart that renders a Deployment which cannot start is a rollout that fails
in the cluster instead of at `helm template`, and the person who typed the
value is no longer watching by then.
*/}}
{{- define "drake.validateGitOps" -}}
{{- $gitops := .Values.github.gitopsPrEnabled | default false -}}
{{- $worker := .Values.github.workerEnabled | default false -}}
{{- if ne (toString $gitops) (toString $worker) -}}
  {{- fail "github.gitopsPrEnabled and github.workerEnabled are one decision: set both or neither" -}}
{{- end -}}
{{- if $gitops -}}
  {{- if not .Values.github.enabled -}}
    {{- fail "github.gitopsPrEnabled requires github.enabled: repository writes need a real GitHub App" -}}
  {{- end -}}
  {{- if not .Values.github.existingSecret -}}
    {{- fail "github.gitopsPrEnabled requires github.existingSecret: the App credential references are mounted from it" -}}
  {{- end -}}
  {{- if not (or .Values.github.appId .Values.github.clientId) -}}
    {{- fail "github.gitopsPrEnabled requires github.appId or github.clientId" -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/* The production runtime-security contract, for every workload that runs
     the Drake application.

     `Settings.validate_runtime_security()` is a property of the APPLICATION,
     not of the public API deployment — so any workload built from the API
     image has to satisfy it, including the internal agent listeners, which
     nothing outside the cluster can even reach.

     That is exactly how a production upgrade failed: the listener carried
     its own CA and surface settings but not these two, so it raised on
     startup, both containers crash-looped, and `--atomic` rolled back.

     Deliberately only the fields the validator REQUIRES. This is not "give
     the listener the API's environment": session, OIDC callback, GitHub and
     exposure settings stay on the workload that actually serves browsers. */}}
{{- define "drake.productionSecurityEnv" -}}
- name: DRAKE_TRUSTED_PROXY_COUNT
  value: "1"
- name: DRAKE_ALLOWED_WEB_ORIGINS
  value: {{ printf "[%q]" (include "drake.publicOrigin" .) | quote }}
{{- end -}}

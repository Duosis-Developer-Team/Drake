{{- define "drake-otel.labels" -}}
app.kubernetes.io/part-of: drake-otel
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* Pod-template labels. The Datadog opt-out sits only on Drake's pods. */}}
{{- define "drake-otel.podLabels" -}}
app.kubernetes.io/part-of: drake-otel
{{- if .Values.datadogOptOut }}
admission.datadoghq.com/enabled: "false"
{{- end }}
{{- end -}}

{{- define "drake-otel.image" -}}
{{- $image := index .root.Values.images .name -}}
{{- if not $image.digest -}}
{{- fail (printf "images.%s.digest is required: production images are pinned by digest" .name) -}}
{{- end -}}
{{- printf "%s@%s" $image.repository $image.digest -}}
{{- end -}}

{{- define "drake-otel.require" -}}
{{- if not .Values.clusterName -}}
{{- fail "clusterName is required: it must equal the Drake catalog cluster_ref" -}}
{{- end -}}
{{- if not .Values.agent.namespaces -}}
{{- fail "agent.namespaces is required: log collection is an allowlist" -}}
{{- end -}}
{{- if not .Values.clickhouse.nodeSelector -}}
{{- fail "clickhouse.nodeSelector is required: the store must be pinned to a node with headroom" -}}
{{- end -}}
{{- if not .Values.clickhouse.existingSecret -}}
{{- fail "clickhouse.existingSecret is required: credentials are created out of band" -}}
{{- end -}}
{{- end -}}

{{- define "drake-otel.securityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "drake-agent.name" -}}
drake-cluster-agent
{{- end -}}

{{- define "drake-agent.labels" -}}
app.kubernetes.io/name: {{ include "drake-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: drake
app.kubernetes.io/component: cluster-agent
{{- end -}}

{{- define "drake-agent.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else if .Values.image.devTag -}}
{{ .Values.image.repository }}:{{ .Values.image.devTag }}
{{- else -}}
{{ fail "image.digest is required (digest-pinned deploys only; image.devTag exists solely for disposable smoke clusters)" }}
{{- end -}}
{{- end -}}

{{- define "drake-agent.name" -}}
drake-cluster-agent
{{- end -}}

{{- define "drake-agent.labels" -}}
app.kubernetes.io/name: {{ include "drake-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: drake
app.kubernetes.io/component: cluster-agent
{{- end -}}

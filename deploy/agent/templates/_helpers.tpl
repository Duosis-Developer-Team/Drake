{{- define "drake-agent.name" -}}
drake-cluster-agent
{{- end -}}

{{- define "drake-agent.labels" -}}
app.kubernetes.io/name: {{ include "drake-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: drake
app.kubernetes.io/component: cluster-agent
{{- end -}}

{{/* imagePullSecrets, or nothing at all.

     Emitting `imagePullSecrets:` with an empty list is not harmless: it is
     a valid field with a meaningless value, and it hides the difference
     between "no pull secret is needed" and "someone forgot to set one".
     This define produces no output when the list is empty, so the pod spec
     simply does not carry the key. */}}
{{- define "drake-agent.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
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

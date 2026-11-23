{{- define "tracesis-agent.labels" -}}
app.kubernetes.io/part-of: tracesis-agent
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/*
The cluster is named three times — for the agent, the collector and
Prometheus — because each is its own chart. Tracesis writes all three from
one row; a hand edit that changes one of them would send telemetry under a
name no query asks for, so the render refuses it.
*/}}
{{- define "tracesis-agent.require" -}}
{{- $c := .Values.cluster -}}
{{- if not $c.id -}}
{{- fail "cluster.id is required: install with the file Tracesis generates for this cluster" -}}
{{- end -}}
{{- if not $c.name -}}
{{- fail "cluster.name is required: install with the file Tracesis generates for this cluster" -}}
{{- end -}}
{{- if ne $c.id .Values.clusterAgent.clusterId -}}
{{- fail "clusterAgent.clusterId must equal cluster.id" -}}
{{- end -}}
{{- if ne $c.name .Values.clusterAgent.clusterName -}}
{{- fail "clusterAgent.clusterName must equal cluster.name" -}}
{{- end -}}
{{- if ne $c.name .Values.telemetry.clusterName -}}
{{- fail "telemetry.clusterName must equal cluster.name" -}}
{{- end -}}
{{- if not .Values.tracesis.serverCA -}}
{{- fail "tracesis.serverCA is required: the agent only talks to the listener this CA signed" -}}
{{- end -}}
{{- if and .Release.IsInstall (not .Values.enrollment.token) -}}
{{- fail "enrollment.token is required on first install: generate the install file in Tracesis again (tokens last ten minutes)" -}}
{{- end -}}
{{- end -}}

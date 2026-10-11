{{/* vim: set filetype=mustache: */}}
{{/*
Expand the name of the chart.
*/}}
{{- define "koordinator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "koordinator.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "koordinator.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "koordinator.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "koordinator.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Lookup existing immutatble resources
*/}}
{{- define "webhookServiceSpec" -}}
{{- $service := lookup "v1" "Service" .Values.installation.namespace "koordinator-webhook-service" -}}
{{- if $service -}}
{{ if $service.spec.clusterIP -}}
clusterIP: {{ $service.spec.clusterIP }}
{{- end }}
{{ if $service.spec.clusterIPs -}}
clusterIPs:
  {{ $service.spec.clusterIPs }}
{{- end }}
{{ if $service.spec.ipFamilyPolicy -}}
ipFamilyPolicy: {{ $service.spec.ipFamilyPolicy }}
{{- end }}
{{ if $service.spec.ipFamilies -}}
ipFamilies:
  {{ $service.spec.ipFamilies }}
{{- end }}
{{ if $service.spec.type -}}
type: {{ $service.spec.type }}
{{- end }}
{{ if $service.spec.ipFamily -}}
ipFamily: {{ $service.spec.ipFamily }}
{{- end }}
{{- end -}}
ports:
- port: 443
  targetPort: {{ .Values.manager.webhook.port }}
selector:
  koord-app: koord-manager
{{- end -}}

{{- define "webhookSecretData" -}}
{{- $secret := lookup "v1" "Secret" .Values.installation.namespace "koordinator-webhook-certs" -}}
{{- if $secret -}}
data:
{{- range $k, $v := $secret.data }}
  {{ $k }}: {{ $v }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "serviceAccountManager" -}}
{{- $sa := lookup "v1" "ServiceAccount" .Values.installation.namespace "koord-manager" -}}
{{- if $sa -}}
secrets:
{{- range $v := $sa.secrets }}
- name: {{ $v.name }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "koordinator.mutatingWebhooks" -}}
{{- $webhooks := list -}}
{{- if not (contains "PodMutatingWebhook=false" .Values.featureGates) -}}
{{- $webhooks = append $webhooks (dict
  "clientConfig" (dict "caBundle" "Cg==" "service" (dict "name" "koordinator-webhook-service" "namespace" .Values.installation.namespace "path" "/mutate-pod"))
  "failurePolicy" .Values.webhookConfiguration.failurePolicy.pods
  "admissionReviewVersions" (list "v1" "v1beta1")
  "sideEffects" "None"
  "timeoutSeconds" .Values.webhookConfiguration.timeoutSeconds
  "name" "mpod-create.koordinator.sh"
  "namespaceSelector" (dict "matchExpressions" (list (dict "key" "control-plane" "operator" "DoesNotExist")))
  "rules" (list (dict "apiGroups" (list "") "apiVersions" (list "v1") "operations" (list "CREATE") "resources" (list "pods") "scope" "*"))) -}}
{{- $webhooks = append $webhooks (dict
  "admissionReviewVersions" (list "v1" "v1beta1")
  "clientConfig" (dict "caBundle" "Cg==" "service" (dict "name" "koordinator-webhook-service" "namespace" .Values.installation.namespace "path" "/mutate-pod"))
  "failurePolicy" .Values.webhookConfiguration.failurePolicy.pods
  "name" "mpod-update.koordinator.sh"
  "namespaceSelector" (dict "matchExpressions" (list (dict "key" "control-plane" "operator" "DoesNotExist")))
  "objectSelector" (dict "matchExpressions" (list (dict "key" "pod.koordinator.sh/mutating-update" "operator" "In" "values" (list "true"))))
  "rules" (list (dict "apiGroups" (list "") "apiVersions" (list "v1") "operations" (list "UPDATE") "resources" (list "pods") "scope" "*"))
  "sideEffects" "None"
  "timeoutSeconds" .Values.webhookConfiguration.timeoutSeconds) -}}
{{- end -}}
{{- if not (contains "ElasticMutatingWebhook=false" .Values.featureGates) -}}
{{- $webhooks = append $webhooks (dict
  "clientConfig" (dict "caBundle" "Cg==" "service" (dict "name" "koordinator-webhook-service" "namespace" .Values.installation.namespace "path" "/mutate-scheduling-sigs-k8s-io-v1alpha1-elasticquota"))
  "failurePolicy" .Values.webhookConfiguration.failurePolicy.elasticquotas
  "admissionReviewVersions" (list "v1" "v1beta1")
  "sideEffects" "None"
  "timeoutSeconds" .Values.webhookConfiguration.timeoutSeconds
  "name" "melasticquota.koordinator.sh"
  "namespaceSelector" (dict "matchExpressions" (list (dict "key" "control-plane" "operator" "DoesNotExist")))
  "rules" (list (dict "apiGroups" (list "scheduling.sigs.k8s.io") "apiVersions" (list "v1alpha1") "operations" (list "CREATE") "resources" (list "elasticquotas") "scope" "*"))) -}}
{{- end -}}
{{- if contains "NodeMutatingWebhook=true" .Values.featureGates -}}
{{- $webhooks = append $webhooks (dict
  "admissionReviewVersions" (list "v1" "v1beta1")
  "clientConfig" (dict "caBundle" "Cg==" "service" (dict "name" "koordinator-webhook-service" "namespace" .Values.installation.namespace "path" "/mutate-node-status"))
  "failurePolicy" .Values.webhookConfiguration.failurePolicy.nodeStatus
  "name" "mnode-status.koordinator.sh"
  "rules" (list (dict "apiGroups" (list "") "apiVersions" (list "v1") "operations" (list "CREATE" "UPDATE") "resources" (list "nodes/status")))
  "timeoutSeconds" .Values.webhookConfiguration.timeoutSeconds
  "sideEffects" "None") -}}
{{- end -}}
{{- if contains "ReservationMutatingWebhook=true" .Values.featureGates -}}
{{- $webhooks = append $webhooks (dict
  "admissionReviewVersions" (list "v1" "v1beta1")
  "clientConfig" (dict "service" (dict "name" "koordinator-webhook-service" "namespace" .Values.installation.namespace "path" "/mutate-reservation"))
  "failurePolicy" .Values.webhookConfiguration.failurePolicy.reservations
  "name" "mreservation-create.koordinator.sh"
  "rules" (list (dict "apiGroups" (list "scheduling.koordinator.sh") "apiVersions" (list "v1alpha1") "operations" (list "CREATE") "resources" (list "reservations")))
  "timeoutSeconds" .Values.webhookConfiguration.timeoutSeconds
  "sideEffects" "None") -}}
{{- end -}}
{{- toYaml $webhooks -}}
{{- end -}}

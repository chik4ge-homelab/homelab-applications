{{ define "__discord_subject" }}[{{ .Status | toUpper }}{{ if eq .Status "firing" }}:{{ .Alerts.Firing | len }}{{ if gt (.Alerts.Resolved | len) 0 }}, RESOLVED:{{ .Alerts.Resolved | len }}{{ end }}{{ end }}] {{ .GroupLabels.SortedPairs.Values | join " " }} {{ if gt (len .CommonLabels) (len .GroupLabels) }}({{ with .CommonLabels.Remove .GroupLabels.Names }}{{ .Values | join " " }}{{ end }}){{ end }}{{ end }}

{{ define "__discord_values_list" }}{{ if len .Values }}{{ $first := true }}{{ range $refID, $value := .Values -}}
{{ if $first }}{{ $first = false }}{{ else }}, {{ end }}{{ $refID }}={{ $value }}{{ end -}}
{{ else }}[no value]{{ end }}{{ end }}

{{ define "__discord_alert_list" }}
{{ range . -}}
### Value
{{ template "__discord_values_list" . }}
{{ if len .Labels -}}
### Labels
{{ range .Labels.SortedPairs -}}
- {{ .Name }} = {{ .Value }}
{{ end -}}
{{- end -}}
{{ if len .Annotations -}}
### Annotations
{{ range .Annotations.SortedPairs -}}
- {{ .Name }} = {{ .Value }}
{{ end -}}
{{- end -}}

{{- $hasSource := .GeneratorURL -}}
{{- $hasDashboard := .DashboardURL -}}
{{- $hasPanel := .PanelURL -}}
{{- $hasSilence := .SilenceURL -}}
{{- if or $hasSource $hasDashboard $hasPanel $hasSilence }}### Links
- {{ if $hasSource -}}[Source](<{{ .GeneratorURL }}>){{- if or $hasDashboard $hasPanel $hasSilence }} / {{ end -}}{{- end -}}
{{- if $hasDashboard -}}[Dashboard](<{{ .DashboardURL }}>){{- if or $hasPanel $hasSilence }} / {{ end -}}{{- end -}}
{{- if $hasPanel -}}[Panel](<{{ .PanelURL }}>){{- if $hasSilence }} / {{ end -}}{{- end -}}
{{- if $hasSilence -}}[Silence](<{{ .SilenceURL }}>){{- end }}
{{- end }}

{{ end -}}
{{ end -}}

{{ define "discord.title" }}{{ template "__discord_subject" . }}{{ end }}

{{ define "discord.message" -}}
{{ if gt (len .Alerts.Firing) 0 -}}
## :fire: Firing
{{ template "__discord_alert_list" .Alerts.Firing }}
{{- end -}}

{{ if gt (len .Alerts.Resolved) 0 -}}
## :green_heart: Resolved
{{ template "__discord_alert_list" .Alerts.Resolved }}
{{- end -}}
{{ end -}}

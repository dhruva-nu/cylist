{{/* The component this release is, refusing anything else. */}}
{{- define "cylist.component" -}}
{{- if not (has .Values.component (list "app" "data")) -}}
{{- fail "set component to \"app\" or \"data\"" -}}
{{- end -}}
{{- .Values.component -}}
{{- end -}}

{{- define "cylist.labels" -}}
app.kubernetes.io/name: cylist
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "cylist.selectorLabels" -}}
app.kubernetes.io/name: cylist
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* Names of the data release's objects, which app releases mount and dial. */}}
{{- define "cylist.postgresName" -}}{{ .Values.dataRelease }}-postgres{{- end -}}
{{- define "cylist.blobsClaim" -}}{{ .Values.dataRelease }}-blobs{{- end -}}

{{/* The app's image, which an app release cannot be installed without. */}}
{{- define "cylist.image" -}}
{{- if not .Values.image.tag -}}
{{- fail "set image.tag to the image to deploy" -}}
{{- end -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}

{{/* The app's non-secret settings as a container env list. */}}
{{- define "cylist.env" -}}
{{- range $name, $value := .Values.app.env }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- if .Values.app.fcmSecret }}
- name: CYLIST_FCM_SERVICE_ACCOUNT_FILE
  value: /run/secrets/fcm/fcm-service-account.json
{{- end }}
{{- end -}}

{{/* The app secret, then each extra one, later ones winning. */}}
{{- define "cylist.envFromSecrets" -}}
- secretRef:
    name: {{ .Values.secrets.app }}
{{- range .Values.app.extraSecrets }}
- secretRef:
    name: {{ . }}
    optional: true
{{- end }}
{{- end -}}

{{/* Restricted-profile container settings for the app (USER cylist, 10001). */}}
{{- define "cylist.appSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
capabilities:
  drop: [ALL]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

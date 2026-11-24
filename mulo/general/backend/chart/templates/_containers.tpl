{{/*
Backend 컨테이너 정의. web Deployment와 scheduler Deployment가 같이 쓴다.
scheduling: 이 Pod에서 @Scheduled 예약 작업을 돌릴지("true"/"false").
*/}}
{{- define "backend.containers" -}}
{{- $r := .root -}}
- name: {{ $r.Values.app.name }}
  image: "{{ $r.Values.image.repository }}:{{ $r.Values.image.tag }}"
  imagePullPolicy: {{ $r.Values.image.pullPolicy }}
  ports:
    - name: {{ $r.Values.service.portName }}
      containerPort: {{ $r.Values.service.targetPort }}
  envFrom:
    - configMapRef:
        name: {{ $r.Values.configMap.name }}
    - secretRef:
        name: {{ $r.Values.externalSecrets.targetName }}
  env:
    # 예약 작업은 scheduler Pod 1개에서만 돈다. web Pod는 끈다(ShedLock이 없어 여러 Pod가 돌리면 중복 실행).
    - name: MULO_SCHEDULING_ENABLED
      value: {{ .scheduling | quote }}
  resources:
    requests:
      cpu: {{ $r.Values.resources.requests.cpu }}
      memory: {{ $r.Values.resources.requests.memory }}
    limits:
      cpu: {{ $r.Values.resources.limits.cpu }}
      memory: {{ $r.Values.resources.limits.memory }}
  securityContext:
    allowPrivilegeEscalation: {{ $r.Values.securityContext.allowPrivilegeEscalation }}
    capabilities:
      drop:
        - {{ $r.Values.securityContext.dropCapability }}
  # 종료 신호(SIGTERM) 전에 잠시 기다린다. 그동안 Envoy·kube-proxy가 이 Pod를 대상에서 뺀다.
  # 바로 종료하면 대상에서 빠지기 전 몇 초 동안 들어온 요청이 끊긴다(Pod 축출·재배치·배포 때).
  lifecycle:
    preStop:
      sleep:
        seconds: {{ $r.Values.lifecycle.preStop.sleepSeconds }}
  startupProbe:
    httpGet:
      path: {{ $r.Values.probes.startup.path }}
      port: {{ $r.Values.probes.startup.port }}
    initialDelaySeconds: {{ $r.Values.probes.startup.initialDelaySeconds }}
    periodSeconds: {{ $r.Values.probes.startup.periodSeconds }}
    failureThreshold: {{ $r.Values.probes.startup.failureThreshold }}
    timeoutSeconds: {{ $r.Values.probes.startup.timeoutSeconds }}
  livenessProbe:
    {{- if eq ($r.Values.probes.liveness.type | default "http") "tcp" }}
    # 프로세스가 포트를 열고 있는지만 본다. DB·Redis가 끊겨도 앱을 재시작하지 않는다.
    tcpSocket:
      port: {{ $r.Values.probes.liveness.port }}
    {{- else }}
    httpGet:
      path: {{ $r.Values.probes.liveness.path }}
      port: {{ $r.Values.probes.liveness.port }}
    {{- end }}
    initialDelaySeconds: {{ $r.Values.probes.liveness.initialDelaySeconds }}
    periodSeconds: {{ $r.Values.probes.liveness.periodSeconds }}
    failureThreshold: {{ $r.Values.probes.liveness.failureThreshold }}
    timeoutSeconds: {{ $r.Values.probes.liveness.timeoutSeconds }}
  readinessProbe:
    httpGet:
      path: {{ $r.Values.probes.readiness.path }}
      port: {{ $r.Values.probes.readiness.port }}
    initialDelaySeconds: {{ $r.Values.probes.readiness.initialDelaySeconds }}
    periodSeconds: {{ $r.Values.probes.readiness.periodSeconds }}
    failureThreshold: {{ $r.Values.probes.readiness.failureThreshold }}
    timeoutSeconds: {{ $r.Values.probes.readiness.timeoutSeconds }}
{{- end }}

#!/usr/bin/env python3
"""MULO 서비스 모니터링 대시보드(mulo-backend.json)를 만든다.

JSON을 손으로 고치지 말고 이 파일을 고친 뒤 다시 실행한다.
    python3 build_dashboard.py > mulo-backend.json

원칙
- 앱 색은 앱마다 고정한다(backend 파랑, frontend 주황, chat 청록, ai-api 노랑, envoy 분홍, scheduler 보라).
- Pod·노드는 IP 대신 이름으로 보이고, 노드는 "역할 · AZ"를 붙인다.
- 위쪽 줄은 지금 괜찮은지 바로 보이는 숫자, 아래로 갈수록 원인을 찾는 상세 지표다.
- 변수(템플릿)를 두지 않는다. 새로고침마다 변수를 다시 조회하지 않아 화면 팝업·깜빡임이 없다.
"""
import json

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}

APP_NS = 'backend|frontend|chat|ai-api|envoy-gateway-system'
# 실제 사용자 요청만 본다. 빼는 것:
# - /actuator: 헬스체크·메트릭 수집
# - /api/notifications/stream: SSE, 연결을 30초씩 열어 두어 응답시간을 왜곡
# - /api/notifications/unread-status: 화면이 주기적으로 자동 호출(폴링)해서 요청 수·Top 10을 덮음
HTTP = 'job="backend",uri!~"/actuator.*|/api/notifications/stream|/api/notifications/unread-status"'
RI = "$__rate_interval"

# 앱 이름 라벨(app): namespace를 쓰되 backend-scheduler와 Envoy 프록시는 따로 부른다. Envoy 컨트롤러는 뺀다.
def app_label(expr):
    e = f'label_replace({expr}, "app", "$1", "namespace", "(.*)")'
    e = f'label_replace({e}, "app", "backend-scheduler", "pod", "backend-scheduler-.*")'
    e = f'label_replace({e}, "app", "envoy", "pod", "envoy-edge-.*")'
    return e

APP_SEL = f'namespace=~"{APP_NS}",pod!~"envoy-gateway-.*"'

# 노드 메타: node, internal_ip, zone(2a/2b/2c), role(general/chat/ai/system/control-plane)
NODE_META = ('max by (node, internal_ip, zone, role) ('
             'label_replace(kube_node_info, "zone", "$1", "provider_id", "aws:///ap-northeast-(.+)/.*")'
             ' * on(node) group_left(role) ('
             'label_replace(kube_node_spec_taint{key="dedicated"}, "role", "$1", "value", "(.*)")'
             ' or label_replace(kube_node_spec_taint{key="node-role.kubernetes.io/control-plane"}, "role", "control-plane", "key", ".*")))')

def by_node(expr):
    """node-exporter(instance=IP:9100) 지표에 node·zone·role을 붙인다."""
    return (f'max by (node, zone, role) (label_replace({expr}, "internal_ip", "$1", "instance", "(.*):9100")'
            f' * on(internal_ip) group_left(node, zone, role) {NODE_META})')

ZONE_OF_NODE = 'max by (node, zone) (label_replace(kube_node_info, "zone", "$1", "provider_id", "aws:///ap-northeast-(.+)/.*"))'

APP_COLORS = {
    "backend": "#2a78d6", "frontend": "#eb6834", "chat": "#1baf7a",
    "ai-api": "#eda100", "envoy": "#e87ba4", "backend-scheduler": "#6250d6",
}
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

def app_overrides():
    return [{"matcher": {"id": "byName", "options": n},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
            for n, c in APP_COLORS.items()]

_id = [0]
def nid():
    _id[0] += 1
    return _id[0]

def target(expr, legend="", ref="A", instant=False, fmt=None, ds=PROM):
    t = {"datasource": ds, "expr": expr, "refId": ref, "legendFormat": legend}
    if instant:
        t.update({"instant": True, "range": False})
    else:
        t.update({"range": True})
    if fmt:
        t["format"] = fmt
    return t

def thresholds(steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}

def stat(title, desc, expr, unit, steps, x, y, w=4, h=4, decimals=None, legend=""):
    fc = {"unit": unit, "thresholds": thresholds(steps), "color": {"mode": "thresholds"}}
    if decimals is not None:
        fc["decimals"] = decimals
    return {"id": nid(), "type": "stat", "title": title, "description": desc, "datasource": PROM,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [target(expr, legend)],
            "fieldConfig": {"defaults": fc, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "value", "graphMode": "area", "textMode": "value",
                        "justifyMode": "center", "orientation": "auto", "showPercentChange": False}}

def ts(title, desc, targets, unit, x, y, w=8, h=8, stack=False, fill=10, overrides=None, legend_calcs=("mean", "max"),
       legend_table=True, decimals=None, minv=None, maxv=None, steps=None, draw="line", interval_step=False):
    custom = {"drawStyle": draw, "lineWidth": 2, "fillOpacity": fill, "showPoints": "never", "spanNulls": False,
              "lineInterpolation": "stepAfter" if interval_step else "linear",
              "stacking": {"mode": "normal" if stack else "none", "group": "A"},
              "axisSoftMin": 0, "gradientMode": "none"}
    if steps:
        custom["thresholdsStyle"] = {"mode": "line+area" if False else "dashed"}
    d = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if decimals is not None:
        d["decimals"] = decimals
    if minv is not None:
        d["min"] = minv
    if maxv is not None:
        d["max"] = maxv
    d["thresholds"] = thresholds(steps or [(None, "transparent")])
    return {"id": nid(), "type": "timeseries", "title": title, "description": desc, "datasource": PROM,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
            "fieldConfig": {"defaults": d, "overrides": overrides or []},
            "options": {"legend": {"displayMode": "table" if legend_table else "list", "placement": "bottom",
                                   "showLegend": True, "calcs": list(legend_calcs), "sortBy": "Max", "sortDesc": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}

def bargauge(title, desc, expr, legend, unit, x, y, w=12, h=9, steps=None, decimals=None):
    d = {"unit": unit, "thresholds": thresholds(steps or [(None, "#2a78d6")]), "color": {"mode": "thresholds"}, "min": 0}
    if decimals is not None:
        d["decimals"] = decimals
    return {"id": nid(), "type": "bargauge", "title": title, "description": desc, "datasource": PROM,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [target(expr, legend, instant=True)],
            "fieldConfig": {"defaults": d, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True,
                        "valueMode": "color", "namePlacement": "left", "sizing": "auto",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "minVizHeight": 16, "maxVizHeight": 22}}

def row(title, y, collapsed=False, panels=None):
    return {"id": nid(), "type": "row", "title": title, "collapsed": collapsed,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": panels or []}

S5 = ',status=~"5.."'

def req_rate(sel=""):
    return f'sum(rate(http_server_requests_seconds_count{{{HTTP}{sel}}}[{RI}]))'

panels = []

# ───────── 1. 한눈에 보기
y = 0
panels.append(row("1. 한눈에 보기 — 지금 괜찮은가", y)); y += 1
panels += [
    stat("요청 수", "backend가 받는 초당 요청 수(/actuator 제외).", req_rate(), "reqps",
         [(None, "#2a78d6")], 0, y, decimals=1),
    stat("5xx 오류 비율", "서버 오류(5xx) 비율. 1% 넘으면 주황, 5% 넘으면 빨강.",
         '(100 * ' + req_rate(S5) + ' / ' + req_rate() + ') or vector(0)', "percent",
         [(None, STATUS["good"]), (1, STATUS["warning"]), (5, STATUS["critical"])], 4, y, decimals=2),
    stat("p95 응답시간", "요청 95%가 이 시간 안에 끝났다. 300ms 넘으면 주황, 1초 넘으면 빨강.",
         f'histogram_quantile(0.95, sum by (le) (rate(http_server_requests_seconds_bucket{{{HTTP}}}[{RI}])))', "s",
         [(None, STATUS["good"]), (0.3, STATUS["warning"]), (1, STATUS["critical"])], 8, y),
    stat("준비 안 된 Pod", "앱(backend·frontend·chat·ai-api·Envoy)에서 원하는 수보다 Ready가 모자란 Pod 수. 0이 정상.",
         f'sum(kube_deployment_spec_replicas{{namespace=~"{APP_NS}"}}) - sum(kube_deployment_status_replicas_available{{namespace=~"{APP_NS}"}})',
         "none", [(None, STATUS["good"]), (1, STATUS["critical"])], 12, y, decimals=0),
    stat("최근 1시간 재시작", "앱 컨테이너가 최근 1시간 동안 재시작한 횟수. 0이 정상.",
         f'sum(increase(kube_pod_container_status_restarts_total{{namespace=~"{APP_NS}"}}[1h])) or vector(0)',
         "none", [(None, STATUS["good"]), (1, STATUS["warning"]), (5, STATUS["critical"])], 16, y, decimals=0),
    stat("DB 연결 대기", "DB 연결을 기다리는 backend 스레드 수(Hikari pending). 0이 정상.",
         'sum(hikaricp_connections_pending{job="backend"}) or vector(0)', "none",
         [(None, STATUS["good"]), (1, STATUS["warning"]), (5, STATUS["critical"])], 20, y, decimals=0),
]
y += 4

# ───────── 2. 트래픽 · 응답시간 · 오류
panels.append(row("2. 트래픽 · 응답시간 · 오류 (backend)", y)); y += 1
code_cls = f'label_replace(sum by (status) (rate(http_server_requests_seconds_count{{{HTTP}}}[{RI}])), "code", "${{1}}xx", "status", "(.)..")'
panels.append(ts("상태 코드별 요청 수", "2xx(정상)·4xx(클라이언트 오류)·5xx(서버 오류)를 쌓아서 본다.",
                 [target(f'sum by (code) ({code_cls})', "{{code}}")], "reqps", 0, y, stack=True, fill=40,
                 overrides=[{"matcher": {"id": "byName", "options": k}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
                            for k, c in [("2xx", "#2a78d6"), ("3xx", "#86b6ef"), ("4xx", STATUS["warning"]), ("5xx", STATUS["critical"])]]))
lat = lambda q: f'histogram_quantile({q}, sum by (le) (rate(http_server_requests_seconds_bucket{{{HTTP}}}[{RI}])))'
panels.append(ts("응답시간 p50 · p95 · p99", "선이 위로 튀면 일부 요청이 느려진 것이다. 점선은 300ms·1초 기준.",
                 [target(lat(0.5), "p50", "A"), target(lat(0.95), "p95", "B"), target(lat(0.99), "p99", "C")],
                 "s", 8, y, fill=0, steps=[(None, "transparent"), (0.3, STATUS["warning"]), (1, STATUS["critical"])],
                 overrides=[{"matcher": {"id": "byName", "options": k}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
                            for k, c in [("p50", "#9ec5f4"), ("p95", "#2a78d6"), ("p99", "#104281")]]))
panels.append(ts("Pod별 요청 수", "backend web Pod마다 받는 요청 수. 고르게 나뉘는지 본다.",
                 [target(f'sum by (pod) (rate(http_server_requests_seconds_count{{{HTTP}}}[{RI}]))', "{{pod}}")],
                 "reqps", 16, y, fill=0))
y += 8
panels.append(bargauge("느린 API Top 10 (p95, 선택한 시간 전체)", "선택한 시간 범위 전체에서 p95가 가장 긴 API.",
                       f'topk(10, histogram_quantile(0.95, sum by (le, method, uri) (rate(http_server_requests_seconds_bucket{{{HTTP}}}[$__range]))) > 0)',
                       "{{method}} {{uri}}", "s", 0, y,
                       steps=[(None, "#2a78d6"), (0.3, STATUS["warning"]), (1, STATUS["critical"])]))
panels.append(bargauge("많이 호출된 API Top 10 (선택한 시간 전체)", "선택한 시간 범위 동안 호출 수가 가장 많은 API.",
                       f'topk(10, sum by (method, uri) (increase(http_server_requests_seconds_count{{{HTTP}}}[$__range])))',
                       "{{method}} {{uri}}", "short", 12, y, decimals=0))
y += 9

# ───────── 3. Pod 배치 · 오토스케일링
panels.append(row("3. Pod 배치 · 오토스케일링", y)); y += 1
panels.append(ts("앱별 Ready Pod 수", "오토스케일링(KEDA/HPA)으로 늘고 줄어든 Pod 수.",
                 [target('sum by (app) (' + app_label('kube_pod_status_ready{' + APP_SEL + ',condition="true"}') + ')', "{{app}}")],
                 "none", 0, y, w=12, h=9, fill=0, decimals=0, overrides=app_overrides(), legend_calcs=("lastNotNull", "max"),
                 interval_step=True))
zone_count = ('count by (app, zone) (' + app_label('kube_pod_info{' + APP_SEL + '}')
              + ' * on(node) group_left(zone) ' + ZONE_OF_NODE + ')')
panels.append({"id": nid(), "type": "table", "title": "AZ별 Pod 수", "datasource": PROM,
               "description": "앱마다 AZ(2a·2b·2c)에 몇 개씩 떠 있는지. AZ 하나가 죽어도 다른 AZ에 남아 있어야 한다.",
               "gridPos": {"x": 12, "y": y, "w": 12, "h": 9},
               "targets": [target(zone_count, "", instant=True, fmt="table")],
               "transformations": [{"id": "groupingToMatrix", "options": {"columnField": "zone", "rowField": "app", "valueField": "Value", "emptyValue": "zero"}},
                                   {"id": "organize", "options": {"renameByName": {"app\\zone": "앱 \\ AZ"}}}],
               "fieldConfig": {"defaults": {"custom": {"align": "center", "cellOptions": {"type": "color-background", "mode": "basic"}},
                                            "thresholds": thresholds([(None, "transparent"), (1, "#cde2fb"), (2, "#86b6ef"), (3, "#3987e5")]),
                                            "color": {"mode": "thresholds"}, "decimals": 0},
                               "overrides": [{"matcher": {"id": "byName", "options": "앱 \\ AZ"},
                                              "properties": [{"id": "custom.cellOptions", "value": {"type": "auto"}}, {"id": "custom.align", "value": "left"}]}]},
               "options": {"showHeader": True, "cellHeight": "sm"}})
y += 9
pod_table = ('max by (app, pod, node, zone) (' + app_label('kube_pod_info{' + APP_SEL + '}')
             + ' * on(node) group_left(zone) ' + ZONE_OF_NODE + ')')
restarts = f'max by (pod) (kube_pod_container_status_restarts_total{{{APP_SEL}}})'
panels.append({"id": nid(), "type": "table", "title": "Pod 목록", "datasource": PROM,
               "description": "지금 떠 있는 앱 Pod가 어느 노드·AZ에 있는지와 재시작 횟수.",
               "gridPos": {"x": 0, "y": y, "w": 24, "h": 9},
               "targets": [target(pod_table, "", "A", instant=True, fmt="table"), target(restarts, "", "B", instant=True, fmt="table")],
               "transformations": [{"id": "joinByField", "options": {"byField": "pod", "mode": "inner"}},
                                   {"id": "organize", "options": {"excludeByName": {"Time": True, "Time 1": True, "Time 2": True, "Value #A": True},
                                                                  "indexByName": {"app": 0, "pod": 1, "zone": 2, "node": 3, "Value #B": 4},
                                                                  "renameByName": {"app": "앱", "pod": "Pod", "zone": "AZ", "node": "노드", "Value #B": "재시작"}}},
                                   {"id": "sortBy", "options": {"sort": [{"field": "앱"}]}}],
               "fieldConfig": {"defaults": {"custom": {"align": "auto"}, "thresholds": thresholds([(None, "transparent")])},
                               "overrides": [{"matcher": {"id": "byName", "options": "재시작"},
                                              "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                                             {"id": "thresholds", "value": thresholds([(None, STATUS["good"]), (1, STATUS["warning"])])},
                                                             {"id": "color", "value": {"mode": "thresholds"}}, {"id": "custom.width", "value": 90}]},
                                             {"matcher": {"id": "byName", "options": "AZ"}, "properties": [{"id": "custom.width", "value": 70}]}]},
               "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}})
y += 9

# ───────── 4. 앱별 CPU · 메모리
panels.append(row("4. 앱별 CPU · 메모리", y)); y += 1
cpu = f'rate(container_cpu_usage_seconds_total{{{APP_SEL},container!="",container!="POD"}}[{RI}])'
panels.append(ts("앱별 CPU 사용량 (합계)", "앱의 모든 Pod CPU를 더한 값(코어).",
                 [target(f'sum by (app) ({app_label(cpu)})', "{{app}}")], "none", 0, y, fill=0, decimals=2,
                 overrides=app_overrides()))
limit_pct = ('max by (app) (' + app_label('100 * sum by (namespace, pod) (' + cpu + ') / sum by (namespace, pod) (kube_pod_container_resource_limits{' + APP_SEL + ',resource="cpu"})') + ')')
panels.append(ts("CPU 한도 대비 사용률 (가장 바쁜 Pod)", "앱에서 가장 바쁜 Pod가 CPU 한도(limit)의 몇 %를 쓰는지. 100%에 가까우면 느려진다.",
                 [target(limit_pct, "{{app}}")], "percent", 8, y, fill=0, decimals=0, overrides=app_overrides(),
                 steps=[(None, "transparent"), (80, STATUS["warning"])]))
thr = ('max by (app) (' + app_label('100 * sum by (namespace, pod) (rate(container_cpu_cfs_throttled_periods_total{' + APP_SEL + ',container!=""}[' + RI + '])) / sum by (namespace, pod) (rate(container_cpu_cfs_periods_total{' + APP_SEL + ',container!=""}[' + RI + ']))') + ')')
panels.append(ts("CPU 제한(throttling) 비율 (가장 심한 Pod)", "CPU 한도 때문에 멈춘 시간 비율. 높으면 응답시간이 튄다.",
                 [target(thr, "{{app}}")], "percent", 16, y, fill=0, decimals=0, overrides=app_overrides(),
                 steps=[(None, "transparent"), (25, STATUS["warning"])]))
y += 8
mem = f'container_memory_working_set_bytes{{{APP_SEL},container!="",container!="POD"}}'
panels.append(ts("앱별 메모리 (합계)", "앱의 모든 Pod 메모리(working set)를 더한 값.",
                 [target(f'sum by (app) ({app_label(mem)})', "{{app}}")], "bytes", 0, y, w=12, fill=0,
                 overrides=app_overrides()))
mem_pct = ('max by (app) (' + app_label('100 * sum by (namespace, pod) (' + mem + ') / sum by (namespace, pod) (kube_pod_container_resource_limits{' + APP_SEL + ',resource="memory"})') + ')')
panels.append(ts("메모리 한도 대비 사용률 (가장 큰 Pod)", "한도(limit)에 닿으면 OOM으로 재시작된다.",
                 [target(mem_pct, "{{app}}")], "percent", 12, y, w=12, fill=0, decimals=0, overrides=app_overrides(),
                 steps=[(None, "transparent"), (85, STATUS["warning"])]))
y += 8

# ───────── 5. backend JVM · 스레드 · DB 풀
panels.append(row("5. backend 내부 (JVM · 스레드 · DB 연결)", y)); y += 1
J = 'job="backend"'
panels.append(ts("Heap 사용률 (Pod별)", "JVM Heap이 최대치의 몇 %를 쓰는지.",
                 [target(f'100 * sum by (pod) (jvm_memory_used_bytes{{{J},area="heap"}}) / sum by (pod) (jvm_memory_max_bytes{{{J},area="heap"}} > 0)', "{{pod}}")],
                 "percent", 0, y, fill=0, decimals=0, steps=[(None, "transparent"), (85, STATUS["warning"])]))
panels.append(ts("GC 멈춤 시간 비율 (Pod별)", "GC 때문에 앱이 멈춘 시간 비율. 높으면 그 순간 응답이 느려진다.",
                 [target(f'100 * sum by (pod) (rate(jvm_gc_pause_seconds_sum{{{J}}}[{RI}]))', "{{pod}}")],
                 "percent", 8, y, fill=0, decimals=1))
panels.append(ts("요청 처리 스레드 사용 중 (Pod별)", "Tomcat이 지금 처리 중인 요청 스레드 수.",
                 [target(f'sum by (pod) (tomcat_threads_busy_threads{{{J}}})', "{{pod}}")],
                 "none", 16, y, fill=0, decimals=0))
y += 8
panels.append(ts("DB 연결: 사용 중 · 대기", "Hikari 연결 풀. 대기가 생기면 DB 연결이 모자란 것이다.",
                 [target(f'sum(hikaricp_connections_active{{{J}}})', "사용 중", "A"),
                  target(f'sum(hikaricp_connections_pending{{{J}}})', "대기", "B"),
                  target(f'sum(hikaricp_connections_max{{{J}}})', "최대", "C")],
                 "none", 0, y, fill=0, decimals=0,
                 overrides=[{"matcher": {"id": "byName", "options": k}, "properties": p} for k, p in [
                     ("사용 중", [{"id": "color", "value": {"mode": "fixed", "fixedColor": "#2a78d6"}}]),
                     ("대기", [{"id": "color", "value": {"mode": "fixed", "fixedColor": STATUS["critical"]}}]),
                     ("최대", [{"id": "color", "value": {"mode": "fixed", "fixedColor": "#898781"}}, {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}])]]))
panels.append(ts("DB 연결 얻는 데 걸린 시간 (평균)", "요청이 DB 연결을 받기까지 기다린 평균 시간.",
                 [target(f'sum(rate(hikaricp_connections_acquire_seconds_sum{{{J}}}[{RI}])) / sum(rate(hikaricp_connections_acquire_seconds_count{{{J}}}[{RI}]))', "평균 대기")],
                 "s", 8, y, fill=0, legend_table=False))
panels.append(ts("JVM 프로세스 CPU (Pod별)", "JVM이 보는 CPU 사용률.",
                 [target(f'100 * process_cpu_usage{{{J}}}', "{{pod}}")], "percent", 16, y, fill=0, decimals=0))
y += 8

# ───────── 6. 노드
panels.append(row("6. 노드 (역할 · AZ)", y)); y += 1
nleg = "{{role}} · {{zone}} · {{node}}"
panels.append(ts("노드 CPU 사용률", "노드 전체 CPU 사용률. 이름은 역할 · AZ · 노드.",
                 [target(by_node(f'100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{{mode="idle"}}[{RI}])))'), nleg)],
                 "percent", 0, y, w=12, h=9, fill=0, decimals=0, maxv=100, steps=[(None, "transparent"), (80, STATUS["warning"])]))
panels.append(ts("노드 메모리 사용률", "노드 전체 메모리 사용률(사용 가능 메모리 기준).",
                 [target(by_node('100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)'), nleg)],
                 "percent", 12, y, w=12, h=9, fill=0, decimals=0, maxv=100, steps=[(None, "transparent"), (85, STATUS["warning"])]))
y += 9
panels.append(bargauge("노드 루트 디스크 사용률", "노드 / 파일시스템 사용률.",
                       by_node('100 * (1 - node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"})'),
                       nleg, "percent", 0, y, h=9, decimals=0,
                       steps=[(None, "#2a78d6"), (75, STATUS["warning"]), (90, STATUS["critical"])]))
panels.append(ts("노드 네트워크 (수신 + 송신)", "노드의 네트워크 처리량.",
                 [target(by_node('sum by (instance) (rate(node_network_receive_bytes_total{device!~"lo|veth.*|cali.*|vxlan.*|tunl.*"}[$__rate_interval]) + rate(node_network_transmit_bytes_total{device!~"lo|veth.*|cali.*|vxlan.*|tunl.*"}[$__rate_interval]))'), nleg)],
                 "Bps", 12, y, w=12, h=9, fill=0))
y += 9

# ───────── 7. 로그 (접어 둠)
log_panels = [
    {"id": nid(), "type": "timeseries", "title": "앱별 오류 로그 수 (1분)", "datasource": LOKI,
     "description": "error·exception·fatal이 들어간 로그 줄 수.",
     "gridPos": {"x": 0, "y": y + 1, "w": 24, "h": 7},
     "targets": [{"datasource": LOKI, "refId": "A", "legendFormat": "{{namespace}}",
                  "expr": 'sum by (namespace) (count_over_time({namespace=~"backend|frontend|chat|ai-api"} |~ "(?i)error|exception|fatal" [1m]))'}],
     "fieldConfig": {"defaults": {"unit": "short", "custom": {"drawStyle": "bars", "fillOpacity": 70, "lineWidth": 1, "stacking": {"mode": "normal", "group": "A"}},
                                  "color": {"mode": "palette-classic"}}, "overrides": app_overrides()},
     "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "desc"}}},
    {"id": nid(), "type": "logs", "title": "backend 오류 로그", "datasource": LOKI,
     "gridPos": {"x": 0, "y": y + 8, "w": 24, "h": 12},
     "targets": [{"datasource": LOKI, "refId": "A", "expr": '{namespace="backend"} |~ "(?i)error|exception|fatal"', "maxLines": 200}],
     "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True, "dedupStrategy": "none"}},
    {"id": nid(), "type": "logs", "title": "backend 전체 로그", "datasource": LOKI,
     "gridPos": {"x": 0, "y": y + 20, "w": 24, "h": 12},
     "targets": [{"datasource": LOKI, "refId": "A", "expr": '{namespace="backend"}', "maxLines": 200}],
     "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True, "dedupStrategy": "none"}},
]
panels.append(row("7. 로그 (Loki) — 펼쳐서 본다", y, collapsed=True, panels=log_panels)); y += 1

dash = {
    "uid": "mulo-stack-details",
    "title": "MULO 서비스 모니터링",
    "description": "MULO v3 prod. 위에서부터 '지금 괜찮은가 → 어디가 문제인가' 순서. build_dashboard.py로 생성.",
    "tags": ["mulo", "prod"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "1m",
    "time": {"from": "now-1h", "to": "now"},
    "timepicker": {"refresh_intervals": ["30s", "1m", "5m", "15m"]},
    "templating": {"list": []},
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
                              "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"}]},
    "links": [],
    "panels": panels,
    "schemaVersion": 41,
    "version": 1,
    "fiscalYearStartMonth": 0,
    "liveNow": False,
    "weekStart": "",
}
print(json.dumps(dash, ensure_ascii=False, indent=2))

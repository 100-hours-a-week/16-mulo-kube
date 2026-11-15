# mulo-kube

MULO 서비스를 AWS 서울 리전의 kubeadm 클러스터(Kubernetes 1.36)에 배포하는 Helm 차트와 values입니다.
Git이 설정의 원본이고, 배포는 Argo CD가 합니다.

## 목차

- [구조](#구조)
- [디렉터리](#디렉터리)
- [설치](#설치)
- [시크릿](#시크릿)
- [도메인과 라우팅](#도메인과-라우팅)
- [앱별 설정](#앱별-설정)
- [자동 확장](#자동-확장)
- [차트 작성 규칙](#차트-작성-규칙)
- [알려진 제약](#알려진-제약)

## 구조

```text
사용자
  │ HTTPS
  ▼
ALB (TLS 종료)
  │ HTTP, NodePort 30080
  ▼
Envoy (edge namespace, general 노드)
  ├─ mulostudio.p-e.kr /        → frontend
  ├─ /api, /image               → backend
  ├─ /ws                        → chat
  └─ argocd / grafana / prometheus 관리 도메인
```

워커는 용도별로 4그룹이고, 각 노드에 `workload=<그룹>` 라벨과 `dedicated=<그룹>:NoSchedule` taint가 걸려 있습니다.

| 노드 그룹 | 올라가는 것 |
|---|---|
| general | frontend, backend, Envoy |
| chat | chat |
| ai | ai-api |
| system | Argo CD, Vault, ESO, Redis, Prometheus, Grafana, Loki, EBS CSI controller 등 |

네트워크는 Calico VXLAN이고, Calico 설치와 NetworkPolicy는 이 저장소 밖에서 관리합니다.

## 디렉터리

```text
mulo/
├── general/
│   ├── frontend/chart      # 정적 웹 (Vite)
│   └── backend/chart       # Spring API
├── chat/chart              # WebSocket 채팅 서버
├── ai/chart                # AI API (내부 전용)
├── system/
│   ├── foundation/chart    # namespace, PriorityClass, ResourceQuota, gp3 StorageClass
│   ├── gateway/chart       # GatewayClass, Gateway, EnvoyProxy, /healthz
│   ├── redis               # Redis StatefulSet (AOF, 5Gi)
│   ├── external-secrets/
│   │   ├── values.yaml     # ESO operator
│   │   └── store           # Vault 연결 (ClusterSecretStore)
│   ├── argocd/
│   │   ├── values.yaml
│   │   ├── bootstrap-values.yaml   # 최초 설치용 (HTTPRoute 끔)
│   │   ├── root-app.yaml           # app-of-apps 루트
│   │   └── apps/                   # 차트별 Application
│   ├── envoy-gateway/      # Envoy Gateway controller
│   ├── ebs-csi/
│   ├── vault/
│   ├── metrics-server/
│   ├── cluster-autoscaler/ # 노드 자동 증설
│   └── monitoring/         # prometheus, grafana, loki, alloy
└── scripts/
    └── install-worker-ecr.sh   # 워커 노드 ECR 인증 설정
```

`chart`가 붙은 디렉터리와 `redis`, `external-secrets/store`는 직접 만든 차트이고, 나머지는 공식 차트에 넘기는 values 파일입니다.

## 설치

노드 준비(ECR 인증, IAM, Calico 점검)와 Vault 초기화 같은 클러스터 작업은 별도 런북으로 관리합니다. 이 절은 그게 끝났다는 전제입니다.

### 1. Argo CD 전까지는 Helm으로

```bash
helm upgrade --install foundation system/foundation/chart --namespace default --wait

helm upgrade --install argocd argo/argo-cd --version 10.17.2 --namespace argocd \
  -f system/argocd/values.yaml -f system/argocd/bootstrap-values.yaml --wait
```

처음엔 Gateway API CRD가 없어서 `bootstrap-values.yaml`로 Argo CD의 HTTPRoute만 끄고 설치합니다.

### 2. 나머지는 Argo CD로

```bash
kubectl apply -f system/argocd/root-app.yaml
```

`system/argocd/apps/`의 Application들이 아래 순서(sync-wave)로 올라갑니다.

| wave | 대상 |
|---|---|
| -3 | foundation |
| -2 | EBS CSI, Envoy Gateway, External Secrets, metrics-server, Cluster Autoscaler |
| -1 | Vault, Vault store, Redis, Gateway, Argo CD 자신 |
| 0 | Loki, Prometheus |
| 1 | Grafana, Alloy |
| 2 | frontend, backend, chat, ai-api |

- wave는 생성 순서만 정합니다. 앞 단계가 준비될 때까지 기다리지 않으므로, CRD나 Secret이 아직 없어서 실패한 건 재시도로 따라잡습니다.
- **Vault만 수동 Sync입니다.** 설정이 seal과 저장소에 바로 닿기 때문입니다. StatefulSet이 OnDelete라 Sync 뒤 `vault-0`을 지워야 새 설정으로 뜹니다.
- Argo CD가 관리하기 시작한 뒤에는 `helm upgrade`나 `helm uninstall`을 직접 쓰지 마세요.

### 공식 차트 버전

| 차트 | 버전 |
|---|---|
| argo/argo-cd | 10.17.2 |
| hashicorp/vault | 0.34.1 |
| aws-ebs-csi-driver | 2.68.0 |
| external-secrets | 2.15.0 |
| envoyproxy/gateway-helm (OCI) | v1.10.0 |
| prometheus-community/kube-prometheus-stack | 93.2.0 |
| grafana-community/grafana | 10.7.0 |
| grafana/loki | 7.6.0 |
| grafana/alloy | 1.15.0 |
| metrics-server | 3.14.0 |
| autoscaler/cluster-autoscaler | 9.60.0 (이미지 v1.36.0) |

버전을 올릴 때는 `system/argocd/apps/`의 `targetRevision`을 바꿉니다.

## 시크릿

비밀 값은 Git에 넣지 않습니다. 흐름은 이렇습니다.

```text
Vault KV (secret/) → ClusterSecretStore mulo-vault → 앱별 ExternalSecret → Kubernetes Secret → envFrom
```

Vault에 넣어야 하는 값:

| Vault 경로 | 필드 | 쓰는 곳 |
|---|---|---|
| `mulo/prod/platform/grafana` | `admin_user`, `admin_password` | Grafana |
| `mulo/prod/shared/service-auth` | `ai_internal_token` | backend, chat, ai-api |
| `mulo/prod/shared/service-auth` | `chat_internal_api_key` | backend, chat |
| `mulo/prod/apps/backend` | `db_password`, `jwt_secret`, `kakao_rest_api_key`, `kma_service_key`, `spotify_client_id`, `spotify_client_secret` | backend |
| `mulo/prod/apps/chat` | `chat_db_password` | chat |
| `mulo/prod/apps/ai` | `database_url`, `anthropic_api_key`, `replicate_api_token` | ai-api |

`shared/service-auth`는 호출하는 쪽과 받는 쪽이 같은 값을 읽도록 한 곳에 모았습니다.

Vault 값을 바꾸면 ESO가 1시간 안에 Secret을 갱신합니다. 환경변수는 Pod가 다시 떠야 바뀌므로 재시작합니다.

```bash
kubectl -n backend rollout restart deployment/backend
```

Redis는 인증 없이 쓰고, 접근 제한은 Calico 정책으로 합니다.

## 도메인과 라우팅

| 도메인 | 연결되는 곳 |
|---|---|
| `mulostudio.p-e.kr` | frontend (`/`), backend (`/api`, `/image`), chat (`/ws`) |
| `api.mulostudio.p-e.kr` | backend (`/api`, `/image`), chat (`/ws`) |
| `argocd.mulostudio.p-e.kr` | Argo CD |
| `grafana.mulostudio.p-e.kr` | Grafana |
| `prometheus.mulostudio.p-e.kr` | Prometheus |

- 경로는 잘라내지 않고 그대로 넘깁니다.
- Gateway는 `mulo.io/gateway-access: "true"` 라벨이 있는 namespace의 HTTPRoute만 받습니다. ai-api, vault, redis에는 이 라벨이 없어서 외부에 열리지 않습니다.
- ALB 헬스체크는 Host 헤더 없이 `/healthz`로 오므로 Envoy가 직접 200을 돌려줍니다.

## 앱별 설정

| 앱 | 포트 | 헬스체크 | 메모 |
|---|---|---|---|
| frontend | 3000 | `/` | `VITE_*` 값은 이미지 빌드 때 들어갑니다. 런타임 설정은 없습니다 |
| backend | 8080 | `/actuator/health` | 아직 GCS SDK를 씁니다. S3 전환 후 배포 예정 |
| chat | 8081 | `/health` | Redis Pub/Sub으로 서버 간 메시지 공유 (`CHAT_DELIVERY_ENABLED=true`) |
| ai-api | 8000 | `/health` | 이미지 하나에서 gateway, 모더레이션, 임베딩, worker가 같이 돕니다 |

이미지 태그는 각 차트의 `values.yaml`에 있고, 바꿔서 커밋하면 Argo CD가 배포합니다.
Pod 수는 HPA가 정합니다. 아래 자동 확장 절을 보세요.

## 자동 확장

트래픽이 늘면 두 단계로 늘어납니다.

```text
CPU 사용률 ↑ → HPA가 Pod를 늘림 → 노드에 자리가 없어 Pending
            → Cluster Autoscaler가 그 역할의 ASG desired를 늘림
            → 새 EC2가 User Data로 kubeadm join (workload 라벨·taint 자동 등록)
            → Pending Pod가 새 노드에 뜸 (general이면 ALB 대상 그룹에도 자동 등록)
```

| 대상 | 최소 | 최대 | 기준 |
|---|---|---|---|
| frontend, backend, chat, ai-api | 2 | 4 | CPU 70% |
| Envoy (edge) | 2 | 4 | CPU 70% |
| general 노드 (ASG) | 1 | 3 | Pending Pod |
| chat 노드 | 1 | 2 | Pending Pod |
| ai 노드 | 1 | 3 | Pending Pod |
| system 노드 | 1 | 2 | Pending Pod |

- Pod 최대값은 부하테스트 전 초기 상한입니다(Cloud Wiki 3.10). 바꾸면 `system/foundation/chart/values.yaml`의 ResourceQuota도 같이 늘려야 합니다. quota는 "최대 Pod 수 + 롤링 업데이트 여유 1개"가 들어가게 잡았습니다.
- 노드 최대값은 Terraform `workers`의 `max`입니다.
- 줄일 때: HPA는 5분(chat 15분, ai 10분) 동안 지켜본 뒤 1분에 1개씩 줄입니다. CA는 requests 합이 50% 미만인 노드가 10분 넘게 이어지면 Pod를 옮기고 노드를 줄입니다. PDB가 앱마다 최소 1개를 남깁니다.
- Redis와 Vault Pod에는 `safe-to-evict: "false"`를 달아서, 이 둘이 있는 system 노드는 줄이지 않습니다.
- 새 노드가 가입하려면 SSM의 가입 토큰이 살아 있어야 합니다. Terraform이 CP에 6시간마다 토큰을 갱신하는 timer를 설치합니다.

확인:

```bash
kubectl get hpa -A
kubectl -n kube-system logs deploy/cluster-autoscaler --tail=50
kubectl -n kube-system get configmap cluster-autoscaler-status -o yaml
```

## 차트 작성 규칙

직접 만든 차트는 읽기 쉬운 쪽을 택했습니다.

- `_helpers.tpl`, `include`, `if`, `range`, `toYaml`, `quote`를 쓰지 않습니다. 템플릿에서 `{{ .Values.x }}`를 바로 참조합니다.
- 그래서 namespace나 quota처럼 반복되는 것도 하나씩 풀어서 씁니다.
- 문자열이어야 하는 값은 템플릿에서 따옴표를 직접 붙입니다.
- 앱 차트 4개는 같은 모양입니다. 하나를 고치면 나머지도 맞춰 주세요.

검증은 이렇게 합니다.

```bash
helm lint --strict general/backend/chart
helm template backend general/backend/chart | kubectl apply --dry-run=server -f -
```

## 알려진 제약

- **Pod별 IAM 역할(IRSA)이 없습니다.** EBS CSI와 Loki는 system 노드의 EC2 역할을 같이 씁니다. system 노드의 다른 Pod도 같은 권한을 볼 수 있습니다.
- **Vault는 1개 Pod입니다.** AWS KMS로 자동 unseal하므로 재시작돼도 사람이 손댈 필요는 없지만, 뜨는 동안에는 시크릿 갱신이 잠깐 멈춥니다(이미 만들어진 Secret은 그대로 있음). TLS 인증서는 self-signed 365일짜리입니다.
- **Redis는 1개 Pod입니다.** AOF(`everysec`)로 키는 남지만, 죽어 있는 동안 Pub/Sub 메시지는 사라집니다.
- **Prometheus 도메인에 인증이 없습니다.** ALB나 Envoy에서 IP 제한을 거는 게 좋습니다.
- **metrics-server는 kubelet 인증서를 검증하지 않습니다**(`--kubelet-insecure-tls`). 검증하려면 노드가 늘 때마다 CSR을 승인해야 해서 자동 증설과 맞지 않습니다. 보안 그룹이 kubelet 포트를 system 노드와 CP에만 엽니다.
- kube-prometheus-stack에서 controller-manager, scheduler, etcd, kube-proxy 수집은 꺼 뒀습니다. kubeadm 기본값이 127.0.0.1에만 메트릭을 열기 때문입니다.
- gp3 StorageClass는 `Retain`입니다. PVC를 지워도 EBS 볼륨이 남으니 직접 정리해야 합니다.
- Loki 로그 보존 기간은 7일, Prometheus는 3일(8GB)입니다.

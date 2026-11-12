# MULO Helm

계정 `840255883218`, 서울 리전, Kubernetes `1.36.6`과 현재 워커 4대 구성을 기준으로 values 20개를 작성했다.
GitHub를 설정 원본으로 사용한다. CP의 파일은 초기 설치에 사용하는 Git 작업 사본이며, 이후 변경도 커밋·푸시하고 Argo CD로 동기화한다.

**워커 ECR 설정부터 진행하는 경우:** [1. Mac에서 푸시](#1-로컬-mac에서-실행--설치-스크립트를-git에-올리기) → [2. 워커에서 실행](#2-워커-노드에서-실행--cp1에서-실행하지-않기) → [3. CP1에서 확인](#3-cp1에서-실행--방금-작업한-워커-확인) → [4. CP1에서 Helm 설치](#4-cp1에서-실행--helm-초기-설치와-gitops-전환).

## 확정한 초기 구성

- 앱 이미지는 ECR에 게시돼 있으며 values에 실제 태그를 지정했다: frontend `1.17.0`, backend `1.16.0`, chat `1.3.0`, AI `1.11.0`. 이미지 Pull 인증과 앱별 의존성을 준비한 뒤 배포한다.
- backend는 현재 GCS SDK를 사용한다. 사용자가 S3 전환 후 배포하기로 했으므로, 지원하지 않는 S3 환경변수를 임의로 추가하지 않았다.
- Vault는 별도 EC2가 아니라 system 워커의 Pod 1개다. TLS·Raft·5Gi PVC·수동 unseal을 사용한다.
- Redis는 system 노드에 StatefulSet 1개로 실행하고 gp3 PVC 5Gi와 AOF를 사용한다. 초기에는 무인증이며 접근 제한은 별도로 관리하는 Calico 정책에서 적용한다. Redis용 Secret은 필요 없다.
- 채팅 서버 간 Redis Pub/Sub 공유는 `CHAT_DELIVERY_ENABLED=true`로 활성화한다. 앱 알림의 Redis 연동은 아직 미구현이므로 별도 설정을 추가하지 않는다.
- Argo CD·Grafana·Prometheus는 HTTPS 관리 도메인을 사용한다. 추가 접근 제어는 사용자가 나중에 구성한다.
- 앱 replicas는 1이다. HPA·KEDA ScaledObject·CronJob·앱 PDB 템플릿은 초기 구성에서 제외했다. Argo CD의 ApplicationSet·SSO·알림도 초기에는 켜지 않는다.
- Calico 설치와 모든 NetworkPolicy는 이 저장소 밖에서 관리한다. RabbitMQ 리소스는 포함하지 않는다.

직접 작성한 차트는 `.tpl` 파일과 `include` helper 없이 YAML에서 `{{ .Values... }}`를 직접 참조한다. `if/else/with` 분기와 `range` 반복을 사용하지 않으며, namespace, quota, PriorityClass, 플랫폼 Secret도 반복문 없이 하나씩 직접 작성한다. 따옴표 함수(`quote`)도 쓰지 않고, 문자열이어야 하는 값만 YAML에 따옴표를 직접 적는다. 공식 차트의 `enabled` 값은 해당 차트의 설정 항목으로 유지한다. `toYaml` 없이 자원·배치·라우팅 항목을 YAML 필드별로 작성한다. 빈 annotations는 제외하고, EBS CSI·Cluster Autoscaler·Loki의 AWS 역할 연결 3곳만 유지한다.

## 실제 설치 전에 남은 작업

2026-11-11 AWS를 읽기 전용으로 조회해 확인한 상태다. values를 채웠다고 AWS 리소스가 생성되는 것은 아니다.

| 항목 | 현재 상태와 필요한 작업 |
|---|---|
| ECR | 네 앱의 게시된 이미지 태그를 각 values에 반영했다. 워커의 ECR 이미지 Pull 인증을 준비한다. |
| Pod IAM/OIDC | OIDC provider와 아래 Pod 역할이 아직 없다. kubeadm issuer/JWKS, IAM 신뢰 정책, projected token과 AWS SDK 환경변수 주입을 준비한다. |
| EBS CSI 역할 | values에는 Terraform에 정의된 예정 ARN `arn:aws:iam::840255883218:role/mulo-v3-prod-ebs_csi`를 넣었다. 역할을 생성한 뒤 설치한다. |
| Autoscaler 역할 | 예정 ARN은 `arn:aws:iam::840255883218:role/mulo-v3-prod-autoscaler`다. 새 워커의 join·ECR bootstrap과 역할별 max 설정은 IaC 코드에 반영했다. 실제 AWS 적용은 별도다. |
| Loki 역할 | 예정 ARN은 `arn:aws:iam::840255883218:role/mulo-v3-prod-loki`다. 실제 Loki S3 버킷 접근 권한이 필요하다. |
| HTTPS | 조회 당시 서울 ACM에 발급·검증 중 인증서가 없고, ALB에는 HTTP 80 리스너만 있었다. 도메인을 포함하는 인증서와 ALB 443 리스너를 먼저 준비한다. |
| 관리 도메인 전달 | IaC의 `platform_services`에 등록한 호스트가 ALB에서 Envoy 대상 그룹으로 전달되는지, DNS가 해당 ALB를 가리키는지 확인한다. |
| Vault TLS | `vault/vault-server-tls` Secret과 `external-secrets/vault-ca` Secret을 준비한다. 인증서·개인키·root token·unseal key는 Git에 넣지 않는다. |

EC2에 붙은 IAM 역할과 Kubernetes Pod의 IRSA 역할은 다르다. ServiceAccount에 role-arn 주석만 넣어도 kubeadm에서 자동으로 token이 주입되는 것은 아니다.

## 앱 소스와 대조한 값

다음 저장소의 main 브랜치 Dockerfile·환경설정·헬스체크·보안 설정을 확인했다.

| 앱 | 포트·probe | 확인한 설정 |
|---|---|---|
| [FE](https://github.com/100-hours-a-week/KTB4-16th-FE) | 3000, `/` | Vite 정적 빌드. `VITE_API_BASE_URL`, `VITE_KAKAO_MAP_APP_KEY`, `VITE_SENTRY_DSN`은 이미지 빌드 때 지정한다. 런타임 ConfigMap으로 바뀌지 않는다. |
| [BE](https://github.com/100-hours-a-week/KTB4-16th-BE) | 8080, `/actuator/health` | `DB_URL`, `DB_USERNAME`, `DB_PASSWORD`, `REDIS_HOST/PORT`, `AI_BASE_URL`, `CHAT_SERVER_BASE_URL` 등의 실제 키를 사용한다. RabbitMQ 선택 기능은 끈다. |
| [AI](https://github.com/100-hours-a-week/KTB4-16th-AI) | 8000, `/health` | 기본 launcher가 Gateway·모더레이션·임베딩·Worker를 한 이미지에서 실행한다. 공개 HTTPRoute는 만들지 않는다. |
| [CHAT](https://github.com/100-hours-a-week/KTB4-16th-CHAT) | 8081, `/health` | `CHAT_DB_*`, `CHAT_BACKEND_URL`, `CHAT_MODERATION_AI_BASE_URL`, `CHAT_WS_ALLOWED_ORIGINS`를 사용한다. WebSocket 경로는 `/ws`다. |

RDS·ECR·Loki 버킷과 NodePort는 현재 Terraform state의 값을 반영했다. 비밀번호와 API 키는 Vault에서 가져온다.
AI provider·RunPod 주소·선택 API 키는 앱 이미지를 배포하는 단계에서 실제 운영 설정과 추가로 맞춘다. 새 이미지의 동작을 실행 검증한 것은 아니다.

### backend의 Vault 연결

`general/backend/chart/templates/deployment.yaml`에는 Vault에서 동기화한 Secret을 읽는 `envFrom.secretRef`를 추가했다. 이름 `<release>-be`, 노드 배치, 자원, probe 설정은 유지한다.
연결 순서는 `Vault KV → mulo-vault ClusterSecretStore → backend ExternalSecret → backend-secrets → backend 환경변수`다. backend Pod가 Vault root token을 직접 사용하는 방식은 아니다.

Vault의 `secret` KV mount 아래에 다음 값을 먼저 등록한다. 실제 비밀번호·토큰은 values나 Git에 적지 않는다.

| Vault 경로 | 등록할 필드 | backend 환경변수 |
|---|---|---|
| `mulo/prod/apps/backend` | `db_password`, `jwt_secret`, `kakao_rest_api_key`, `kma_service_key`, `spotify_client_id`, `spotify_client_secret` | `DB_PASSWORD`, `JWT_SECRET`, `KAKAO_REST_API_KEY`, `KMA_SERVICE_KEY`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` |
| `mulo/prod/shared/service-auth` | `ai_internal_token` | `AI_INTERNAL_TOKEN` |
| `mulo/prod/shared/service-auth` | `chat_internal_api_key` | `CHAT_INTERNAL_API_KEY`, `CHAT_AUTH_VERIFY_API_KEY` |

`MULO_MUSIC_SEARCH_RATE_LIMIT_NAMESPACE`는 비밀 값이 아니므로 ConfigMap에 `mulo:prod:music-search-rate-limit`으로 설정했다.
Vault·ESO 설치와 KV 등록이 끝나고 실제 앱 이미지 태그와 S3 전환이 준비된 뒤 backend 차트를 배포한다. Secret이 생성되기 전에는 Pod가 시작되지 않는다.

```sh
kubectl get clustersecretstore mulo-vault
kubectl get externalsecret backend-secrets --namespace backend
kubectl describe externalsecret backend-secrets --namespace backend
```

Vault 값을 변경하면 ESO 동기화 완료 후 Pod를 재시작해야 환경변수가 갱신된다. Helm release 이름을 `backend`로 설치한 경우에는 `kubectl rollout restart deployment/backend-be --namespace backend`를 사용한다.
Deployment는 spec.replicas를 계속 출력하므로 향후 HPA를 사용할 때 GitOps 동기화 정책도 함께 검토한다.

## Namespace와 경로

[5단계 설계](https://github.com/100-hours-a-week/KTB4-16th-wiki/wiki/Cloud-Wiki-5)의 서비스별 격리와 edge Gateway 배치를 현재 이미지 단위에 적용했다.

| Namespace | 경로·용도 |
|---|---|
| frontend | `general/frontend/chart` |
| backend | `general/backend/chart` |
| chat | `chat/chart` |
| ai-api | `ai/chart` |
| redis | `system/redis` |
| edge | `system/gateway/chart`: Gateway와 Envoy 데이터 플레인 |
| envoy-gateway-system | `system/envoy-gateway`: Envoy Gateway controller |
| argocd | `system/argocd` |
| vault | `system/vault` |
| external-secrets | `system/external-secrets`, `system/external-secrets/store` |
| keda | `system/autoscaling/keda` |
| monitoring | `system/monitoring/*` |

foundation이 위 namespace 12개와 PriorityClass·앱 ResourceQuota·gp3 StorageClass를 만든다.
EBS CSI·Metrics Server·Cluster Autoscaler는 기존 kube-system을 사용한다. Calico namespace는 별도로 관리한다.
일반 앱과 edge는 general, chat은 chat, AI는 ai, 운영 도구는 system 노드의 workload 라벨을 선택한다.

## 관리 도메인과 Gateway

| 도메인 | HTTPRoute가 연결하는 Service |
|---|---|
| `argocd.mulostudio.p-e.kr` | `argocd/argocd-server:80` |
| `grafana.mulostudio.p-e.kr` | `monitoring/grafana:80` |
| `prometheus.mulostudio.p-e.kr` | `monitoring/prometheus-kube-prometheus-prometheus:9090` |

HTTPS는 ALB에서 종료하고 ALB→Envoy→Service는 내부 HTTP로 전달한다. Argo CD의 `server.insecure: true`는 이 내부 연결을 위한 설정이다.
공식 차트가 HTTPRoute를 생성하도록 values에 작성했다. Terraform의 `platform_http_routes` 출력은 별도로 중복 적용하지 않는다.
Prometheus Route에는 사용자 선택에 따라 인증·IP 제한을 추가하지 않았다.
Gateway 허용 라벨은 frontend/backend/chat/edge/argocd/monitoring에 지정했다. Vault·AI·Redis는 내부 전용이다.
앱의 `/api`, `/image`는 backend로, `/ws`는 chat으로 전달하고 경로를 제거하지 않는다.

## 1. 로컬 Mac에서 실행 — 설치 스크립트를 Git에 올리기

**지금 필요한 순서: Mac에서 푸시 → general 워커에서 스크립트 실행 → CP1에서 Ready 확인 → 나머지 워커도 한 대씩 반복 → Helm 설치.** 이미 끝낸 join·workload 라벨·dedicated taint는 다시 설정하지 않는다.

```bash
# 실행 장소: 내 Mac의 터미널. Git 저장소 루트로 이동한다.
cd ~/Desktop/mulostudio
# 이번에 만든 설치 스크립트와 설명만 커밋 대상으로 지정한다.
git add mulo/scripts/install-worker-ecr.sh mulo/README.md
# 변경 내용을 기록한다.
git commit -m "Automate worker ECR credential provider setup"
# 워커에서 내려받을 수 있도록 원격 저장소에 올린다.
git push origin main
```

위 푸시를 완료한 뒤 2번을 실행한다. 푸시 전에는 새 스크립트 URL에서 404가 나올 수 있다. AWS 액세스 키·ECR 비밀번호·join 토큰은 이 스크립트나 values에 넣지 않는다.

## 2. 워커 노드에서 실행 — CP1에서 실행하지 않기

> **여기부터는 워커 EC2의 SSM 터미널이다. CP1 터미널이 아니다.**
> 현재 접속했던 general 워커 `10.60.16.210`부터 진행한다. CP에서 실행하면 스크립트가 변경 전에 중단한다.

| 순서 | 접속할 워커 | EC2 인스턴스 ID | 사설 IP |
|---|---|---|---|
| 1 | general | `i-04a602838378df387` | `10.60.16.210` |
| 2 | system | `i-042dbb16e93fc9592` | `10.60.18.189` |
| 3 | chat | `i-0ac04ad11a07e767f` | `10.60.17.244` |
| 4 | ai | `i-02b7b17fe6380b941` | `10.60.17.36` |

AWS 콘솔 → EC2 → 위 인스턴스 선택 → 연결 → Session Manager로 접속한다. **각 워커에서 아래 블록 전체를 한 번씩 실행한다.** 어느 디렉터리에 있어도 된다. 워커에 Helm 저장소 전체를 clone할 필요는 없다. 아래 코드 내용만 복사하고, 코드 블록의 백틱이나 `root@...#` 프롬프트는 복사하지 않는다.

```bash
# 실행 장소: 해당 워커의 SSM 터미널. 다운로드용 임시 파일을 만든다.
ecr_setup_file="$(mktemp /tmp/mulo-worker-ecr-download.XXXXXX)" &&
# 다운로드가 성공했을 때만 설치를 실행한다. 실패한 파일은 실행하지 않는다.
curl -fsSL --connect-timeout 10 --max-time 60 \
  https://raw.githubusercontent.com/100-hours-a-week/16-mulo-kube/main/mulo/scripts/install-worker-ecr.sh \
  -o "$ecr_setup_file" &&
# 관리자 권한으로 설치·설정·재시작·확인을 한 번에 진행한다.
sudo bash "$ecr_setup_file"
```

처음 설치할 때는 도구 다운로드와 컴파일에 몇 분이 걸릴 수 있다. GitHub·Ubuntu 패키지 저장소·Go 모듈 저장소·ECR로 나갈 수 있는 네트워크가 필요하다. 중간에 실패하면 화면에 나온 **실패한 단계**를 확인한다. 이 블록은 다시 실행할 수 있고, 설치된 인증 프로그램은 재사용한다.

정상 완료 문구:

```text
[worker-ecr] 완료: kubelet active / ECR 인증 성공 / 시작 옵션 2개 적용
```

이 문구를 확인한 뒤 **3번을 CP1에서 수행한다.** 확인이 끝나면 다음 워커로 이동해 2번부터 반복한다. 기존 워커 4대에는 각각 적용해야 한다.

### 스크립트가 자동으로 하는 일

1. CP 여부·가입 상태·표준 kubeadm 시작 명령을 검사한다. CP 또는 다른 시작 구성이면 변경 전에 중단한다.
2. 없는 도구를 설치하고, 실행 파일이 없을 때만 공식 `cloud-provider-aws v1.36.0`의 고정된 커밋으로 ECR 인증 프로그램을 빌드한다. Kubernetes 패키지를 설치하거나 업그레이드하지 않는다.
3. 해당 EC2에 붙은 IAM 역할로 ECR 인증을 검사한다. 인증값은 화면이나 Git에 출력하지 않는다. 이미지 전체를 실제 Pull하는 검증은 앱 배포 후 수행한다.
4. 기존 ECR 설정을 root 전용 `/var/backups/mulo-worker-ecr/`에 백업하고, 인증 YAML과 systemd의 ECR 옵션을 저장한다. workload·taint·provider-id·기존 `KUBELET_EXTRA_ARGS`는 유지한다.
5. systemd 설정을 다시 읽고, 필요하면 **해당 워커의 kubelet만** 재시작한다. 실행 중인 kubelet에 ECR 옵션 2개가 적용됐는지 확인한다. 설정 적용·재시작이 실패하면 이전 설정 복구를 시도한다.

수동으로 실행 파일을 복사하거나 kubelet 옵션을 터미널에 입력할 필요가 없다. 현재처럼 YAML·systemd 파일만 있고 실행 파일이 없는 상태에서도 이 스크립트를 사용한다.

## 3. CP1에서 실행 — 방금 작업한 워커 확인

> **여기는 CP1 `10.60.16.44`의 관리자 터미널이다. 설치 스크립트는 CP1에서 실행하지 않는다.**

```bash
# 실행 장소: CP1. 전체 노드가 Ready인지, workload가 올바른지 확인한다.
kubectl get nodes -o wide -L workload
# 기존 역할별 dedicated taint가 유지되는지 확인한다.
kubectl get nodes -o custom-columns='NAME:.metadata.name,WORKLOAD:.metadata.labels.workload,TAINTS:.spec.taints'
```

방금 작업한 워커가 Ready여야 다음 워커를 진행한다. Ready로 돌아오지 않으면 **문제가 난 워커에서** 아래 명령으로 상태를 확인한다.

```bash
# 실행 장소: 문제가 난 워커. kubelet 상태와 최근 오류를 본다.
sudo systemctl status kubelet --no-pager
sudo journalctl -u kubelet -n 80 --no-pager
# ECR 옵션이 실제 프로세스에 적용됐는지 확인한다. 아무것도 안 나오면 미적용이다.
ps -ww -C kubelet -o args= | tr ' ' '\n' | sed -n '/^--image-credential-provider-/p'
```

**앞으로 ASG가 만드는 새 워커:** `v3-terraform`의 Launch Template User Data에도 같은 설치 스크립트를 포함했다. 해당 Terraform 변경을 plan·apply한 뒤 새로 생성된 EC2는 도구 설치 → ECR 설정 → SSM 조회 → join·label·taint 등록을 자동 실행한다. 기존 EC2에는 User Data가 다시 실행되지 않으므로 위 2번이 필요하다. 기존 EC2를 자동 교체하는 설정은 추가하지 않았다.

Terraform 적용 순서는 로컬 `~/Desktop/v3-terraform/docs/worker-autojoin/README.md`의 6절을 따른다. 새 AMI를 만들 때 ECR 실행 파일까지 미리 넣으면 첫 부팅의 컴파일 시간을 줄일 수 있다. 이미 join한 현재 워커를 그대로 AMI로 만들지는 않는다.

Pod의 AWS API 인증(OIDC)과 kubelet의 이미지 Pull 인증은 별도다. 이 스크립트는 EC2 IAM 역할로 이미지 Pull 인증을 준비한다. [Kubernetes 공식 설정](https://kubernetes.io/docs/tasks/administer-cluster/kubelet-credential-provider/), [AWS provider 설명](https://cloud-provider-aws.sigs.k8s.io/credential_provider/)

## 4. CP1에서 실행 — Helm 초기 설치와 GitOps 전환

아래 명령은 모두 **CP1(10.60.16.44)** 에서 실행한다. 워커 4대의 ECR 설정과 Ready 확인을 먼저 끝낸다. CP1의 기존 clone 경로는 `/home/16-mulo-kube/mulo`다. 저장소를 아직 clone하지 않았다면 아래 준비 명령을 사용한다. CP에서만 바꾸고 커밋하지 않은 파일은 운영 원본으로 사용하지 않는다.
초기에는 foundation과 Argo CD를 Helm으로 설치한다. 이후 각 차트와 values를 Argo CD Application에 연결한다.
이 명령들은 CP1에서 실행한다. CP1은 관리 명령을 실행하는 장소이며, Argo CD Pod는 values의 workload=system 설정에 따라 system 워커에 배치된다. Argo CD를 설치한 뒤 Git 저장소·브랜치·개별 차트 경로를 Application에 연결한다. 아직 설치 전인 지금은 Application 미등록이 푸시를 막는 문제는 아니다.
아래 나머지 Helm 명령은 초기 점검에 사용할 수 있다. Argo CD가 소유한 뒤에는 같은 리소스를 수동 Helm과 번갈아 변경하지 않는다.

```bash
# 실행 장소: CP1. 이미 clone했다면 다시 clone하지 않는다.
cd /home
# 최초 clone에만 실행한다.
git clone https://github.com/100-hours-a-week/16-mulo-kube.git
# 차트가 있는 디렉터리로 이동한다.
cd /home/16-mulo-kube/mulo
```

기존 clone이 있다면 CP1에서 `git -C /home/16-mulo-kube pull --ff-only`로 갱신한다. 로컬 변경 때문에 실패하면 파일을 강제로 덮어쓰지 말고 변경 내용을 확인한다.

```sh
# 실행 장소: CP1.
cd /home/16-mulo-kube/mulo
helm repo add argo https://argoproj.github.io/argo-helm
helm repo add hashicorp https://helm.releases.hashicorp.com
helm repo add aws-ebs-csi-driver https://kubernetes-sigs.github.io/aws-ebs-csi-driver
helm repo add external-secrets https://charts.external-secrets.io
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add grafana-community https://grafana-community.github.io/helm-charts
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm repo add autoscaler https://kubernetes.github.io/autoscaler
helm repo add kedacore https://kedacore.github.io/charts
helm repo update

helm upgrade --install foundation system/foundation/chart --namespace default --wait
helm upgrade --install argocd argo/argo-cd --version 10.17.2 --namespace argocd -f system/argocd/values.yaml -f system/argocd/bootstrap-values.yaml --wait
kubectl -n argocd get pods -o wide
```

최초 설치에는 Gateway API CRD가 없을 수 있어 Git에 둔 bootstrap-values.yaml로 Route 생성만 끈다. Envoy Gateway와 Gateway가 준비된 뒤 Argo CD Application에는 기본 values.yaml만 연결해 Route를 생성한다.
도메인 HTTPS를 준비하기 전의 관리자 점검은 CP의 loopback으로 port-forward하고 SSM 터널을 통해 접속한다.
현재 Argo CD 백엔드는 HTTP 모드이므로 bootstrap 점검은 `kubectl -n argocd port-forward svc/argocd-server 8080:80`이다.
도메인으로 정상 접속한 뒤에는 values의 HTTPS URL을 사용한다.

### 스토리지와 Vault

EBS CSI용 IAM/OIDC가 준비된 뒤 설치한다. StorageClass만 생성돼 있는 상태에서는 Vault·Prometheus·Loki PVC가 실제 볼륨을 받지 못할 수 있다.

```sh
helm upgrade --install aws-ebs-csi-driver aws-ebs-csi-driver/aws-ebs-csi-driver --version 2.68.0 --namespace kube-system -f system/ebs-csi/values.yaml --wait
kubectl get storageclass gp3
```

새 Vault 설치의 TLS 준비 예시다. 파일은 Git 작업 폴더 밖에 보관한다. 기존 Vault의 인증서를 바꾸는 작업으로 반복 실행하지 않는다.

```sh
mkdir -p "$HOME/vault-bootstrap"
cd "$HOME/vault-bootstrap"
umask 077
openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 365 \
  -keyout vault.key -out vault.crt \
  -subj "/CN=vault.vault.svc.cluster.local" \
  -addext "subjectAltName=DNS:vault,DNS:vault.vault,DNS:vault.vault.svc,DNS:vault.vault.svc.cluster.local,DNS:localhost,IP:127.0.0.1"
kubectl -n vault create secret generic vault-server-tls --from-file=tls.crt=vault.crt --from-file=tls.key=vault.key --from-file=ca.crt=vault.crt
kubectl -n external-secrets create secret generic vault-ca --from-file=ca.crt=vault.crt
cd /home/16-mulo-kube/mulo
helm upgrade --install vault hashicorp/vault --version 0.34.1 --namespace vault -f system/vault/values.yaml
kubectl -n vault get pods,pvc -o wide
```

최초 설치에서는 --wait를 사용하지 않는다. 새 저장소에 처음 한 번 `vault operator init`을 실행하고 나온 unseal key와 root token을 Git 밖에 보관한다.
기본 threshold는 서로 다른 키 3개다. unseal 명령을 세 번 실행해 각각 다른 키를 입력한다.

```sh
kubectl -n vault exec -it vault-0 -- vault operator init
kubectl -n vault exec -it vault-0 -- vault operator unseal
kubectl -n vault exec -it vault-0 -- vault operator unseal
kubectl -n vault exec -it vault-0 -- vault operator unseal
kubectl -n vault exec -it vault-0 -- vault status
```

Pod가 재시작하면 수동 unseal을 다시 수행한다. PVC 데이터는 유지된다. 현재 1 Pod 구성은 고가용성을 제공하지 않는다.

### Vault의 값과 ESO

Vault 안에서 로그인하고 KV v2와 Kubernetes auth를 준비한다. 비밀번호·토큰은 이 문서의 명령에 직접 붙여 넣지 않고 UI 또는 보호된 입력 방식으로 넣는다.

```sh
kubectl -n vault exec -it vault-0 -- sh
vault login
vault secrets enable -path=secret kv-v2
vault auth enable kubernetes
vault write auth/kubernetes/config kubernetes_host=https://kubernetes.default.svc:443
vault policy write mulo-prod-eso - <<'HCL'
path "secret/data/mulo/prod/platform/*" { capabilities = ["read"] }
path "secret/data/mulo/prod/apps/*" { capabilities = ["read"] }
path "secret/data/mulo/prod/shared/*" { capabilities = ["read"] }
HCL
vault write auth/kubernetes/role/mulo-prod-eso bound_service_account_names=vault-auth bound_service_account_namespaces=external-secrets audience=vault policies=mulo-prod-eso ttl=1h
exit
```

KV mount는 secret이고 아래는 mount 아래의 경로다. shared의 두 토큰은 호출하는 서비스와 받는 서비스가 같은 값을 참조하도록 맞췄다.

| 경로 | 필드 |
|---|---|
| `mulo/prod/platform/grafana` | `admin_user`, `admin_password` |
| `mulo/prod/shared/service-auth` | `ai_internal_token`, `chat_internal_api_key` |
| `mulo/prod/apps/backend` | `db_password`, `jwt_secret`, `kakao_rest_api_key`, `kma_service_key`, `spotify_client_id`, `spotify_client_secret` |
| `mulo/prod/apps/ai` | `database_url`, `anthropic_api_key`, `replicate_api_token` |
| `mulo/prod/apps/chat` | `chat_db_password` |

AI의 DATABASE_URL은 앱이 받는 PostgreSQL 드라이버 형식과 실제 RDS 주소·사용자·비밀번호를 맞춘다. 앱별 선택 provider의 추가 키는 앱 배포 시 확정한다.
Redis는 현재 무인증이므로 Redis 비밀번호·ACL Secret을 만들 필요가 없다.

```sh
cd /home/16-mulo-kube/mulo
helm upgrade --install external-secrets external-secrets/external-secrets --version 2.15.0 --namespace external-secrets -f system/external-secrets/values.yaml --wait
helm upgrade --install vault-store system/external-secrets/store --namespace external-secrets
kubectl get clustersecretstore
kubectl get externalsecret -A
```

ClusterSecretStore Ready=True와 ExternalSecret 동기화 상태를 확인한다. `vault.namespace`는 Vault Enterprise 항목이므로 빈 문자열을 유지한다.

### Redis·Gateway·관측 도구

EBS CSI와 gp3 StorageClass가 준비된 뒤 Redis를 설치한다. 앱 접속 주소는 `redis.redis.svc.cluster.local:6379`를 사용한다. `redis-headless`는 StatefulSet의 고정 Pod DNS용이다.
이전 Redis Deployment가 이미 있다면 아래의 전환 절차로 기존 Pod를 먼저 종료한 뒤 Redis Helm 명령을 실행한다.
Envoy 데이터 플레인의 NodePort는 Terraform target group의 30080, health 경로는 /healthz와 맞췄다.

```sh
helm upgrade --install redis system/redis --namespace redis --wait
helm upgrade --install envoy-gateway oci://docker.io/envoyproxy/gateway-helm --version v1.10.0 --namespace envoy-gateway-system -f system/envoy-gateway/values.yaml --wait
helm upgrade --install mulo-gateway system/gateway/chart --namespace edge
helm upgrade --install loki grafana/loki --version 7.6.0 --namespace monitoring -f system/monitoring/loki/values.yaml --wait
helm upgrade --install prometheus prometheus-community/kube-prometheus-stack --version 93.2.0 --namespace monitoring -f system/monitoring/prometheus/values.yaml --wait
helm upgrade --install grafana grafana-community/grafana --version 10.7.0 --namespace monitoring -f system/monitoring/grafana/values.yaml --wait
helm upgrade --install alloy grafana/alloy --version 1.15.0 --namespace monitoring -f system/monitoring/alloy/values.yaml --wait
```

Redis의 AOF는 대화 문맥·로그아웃 표식 같은 키를 보존한다. `appendfsync everysec`이므로 비정상 종료 직전 약 1초의 변경은 유실될 수 있다. Pub/Sub은 연결이 끊긴 동안의 메시지를 저장하거나 재전송하지 않으므로, 재접속 후 채팅 기록은 앱의 DB 조회로 다시 동기화해야 한다. StatefulSet 1개는 자동 장애 전환 구성이 아니다.

이전 Redis Deployment를 이미 설치했다면 채팅을 중단할 시간을 정해 기존 Pod를 먼저 종료하고 위 Redis Helm 명령으로 전환한다. 기존 메모리 키는 새 PVC로 자동 이전되지 않으므로 필요한 키의 백업·복구를 먼저 준비한다. 처음 설치하는 경우에는 아래 종료 명령이 필요 없다.

```sh
kubectl scale deployment redis --replicas=0 --namespace redis
kubectl wait --for=delete pod --selector app=redis --namespace redis --timeout=120s
```

전환 후 `kubectl get statefulset,pod,pvc --namespace redis`로 `redis-0`과 PVC를 확인한다. 이후 redis.conf 값을 바꾸면 `kubectl rollout restart statefulset/redis --namespace redis`로 재시작해 반영한다. StatefulSet이나 Helm release를 지워도 데이터 PVC는 자동 삭제되지 않는다.

Grafana는 Vault의 관리자 Secret 동기화 후 설치한다. Loki는 S3 Pod IAM 권한이 필요하고, Prometheus·Loki PVC에는 EBS CSI가 필요하다.
Grafana의 이전 `grafana/grafana 10.5.15`는 deprecated 상태다. 유지보수 저장소인 `grafana-community/grafana 10.7.0`으로 옮겼고 Grafana 앱 버전 12.3.1은 유지했다. 기존 values의 관리자 Secret·HTTPS Route·system 노드 배치를 다시 검증했다. Loki·Alloy는 기존 grafana 저장소를 사용한다. [이전 안내](https://github.com/grafana/helm-charts/blob/main/charts/grafana/README.md)
Alloy의 수집 설정은 values에 포함했다. Prometheus의 기본 Kubernetes 수집과 별개로 앱 수집에는 실제 메트릭 endpoint와 ServiceMonitor가 필요하다.
Metrics Server·Autoscaler·KEDA의 values도 작성했다. 공식 차트 버전은 Metrics Server `3.14.0`, Cluster Autoscaler `9.60.0`, KEDA `2.21.0`을 사용한다. 나머지 공식 차트 버전은 위 설치 명령의 `--version`에 명시했다. 자동 확장은 현재 비활성 상태로 시작한다.

## 검증과 제한

워커 ECR 설치 스크립트는 설치 파일 누락·재실행·CP 차단·인증 실패·재시작 실패·도구 자동 설치 등을 모의 환경에서 13개 시나리오로 검증했다. Terraform 검증과 모의 테스트 20개도 통과했다. 실제 AWS plan은 Launch Template 4개와 ASG 4개 변경, EC2 교체·삭제 없음이다. 실제 워커에서의 설치·이미지 Pull과 AWS apply는 이 문서의 실행 단계에서 확인한다.

Kubernetes 1.36.6 기준으로 로컬 차트 8개와 공식 차트 12개를 기본 values 그대로 lint·렌더링했다. 별도 bootstrap override는 Argo CD 초기 설치에서 HTTPRoute가 생성되지 않는지 확인했다.
namespace 12개, 관리 UI Route의 Service·포트·Gateway 참조, Redis StatefulSet·PVC·무인증 구성, Vault의 system 배치·PVC, 정책 미생성을 확인했다.
Redis 전환 후 로컬 차트 8개를 다시 lint·렌더링했고, 임시 로컬 Redis에서 AOF 기록 후 강제 종료·재실행 시 키 복구를 확인했다. 실제 배포 이미지와 EBS 연결 검증은 클러스터 설치 후 진행한다.
backend Deployment에는 요청에 따라 Vault Secret을 읽는 envFrom.secretRef만 추가했다. 이름·배치·자원·probe 설정은 기존과 동일하다. 실제 클러스터에 설치하거나 AWS 리소스를 변경하지 않았다.
설정된 주요 system controller의 requests 합계는 약 1.23 CPU / 3456MiB이며, DaemonSet·노드 예약분·일부 생성 sidecar를 포함한 실제 사용량 검증은 설치 후 별도로 수행한다.

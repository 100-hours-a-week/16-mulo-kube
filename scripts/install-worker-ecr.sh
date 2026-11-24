#!/bin/bash
# 실행 장소: system/general/chat/ai 워커. CP에서는 실행을 거부한다.
# 기존 워커는 인자 없이 실행한다. --bootstrap은 ASG의 가입 전 준비용이다.
set -Eeuo pipefail
umask 077

VERSION="v1.36.0"
SOURCE_COMMIT="ee741db1d3fa0bd8db0e867e58bad8c4b9b6e1ea"
REGISTRY="840255883218.dkr.ecr.ap-northeast-2.amazonaws.com"
REGION="ap-northeast-2"
BIN_DIR="/opt/kubelet-credential-providers"
PROVIDER="$BIN_DIR/ecr-credential-provider"
CONFIG="/etc/kubernetes/ecr-credential-provider.yaml"
DROPIN="/etc/systemd/system/kubelet.service.d/20-ecr-credential-provider.conf"
BACKUP_ROOT="/var/backups/mulo-worker-ecr"
BOOTSTRAP=false
WORK_DIR=""
BACKUP_DIR=""
CONFIG_CHANGED=false
RESTART_ATTEMPTED=false
STAGE="시작 전 검사"

log() { printf '[worker-ecr] %s\n' "$*"; }
die() { log "중단: $*" >&2; exit 1; }

# 실패한 작업 이름과 종료 코드만 출력한다. ECR 인증 응답은 출력하지 않는다.
on_error() {
  local status="$1"
  trap - ERR
  log "실패한 단계: $STAGE (종료 코드 $status)" >&2
  if [[ "$CONFIG_CHANGED" == true && -n "$BACKUP_DIR" ]]; then
    log "이전 kubelet 설정을 복구합니다: $BACKUP_DIR" >&2
    for name in config dropin; do
      local target="$CONFIG"
      [[ "$name" == dropin ]] && target="$DROPIN"
      if [[ -f "$BACKUP_DIR/$name" ]]; then
        cp -p "$BACKUP_DIR/$name" "$target" || true
      else
        rm -f "$target"
      fi
    done
    systemctl daemon-reload || true
    if [[ "$RESTART_ATTEMPTED" == true ]]; then
      systemctl restart kubelet || true
    fi
  fi
  log '상세 실행 상태: sudo systemctl status kubelet --no-pager' >&2
  exit "$status"
}
trap 'on_error "$?"' ERR
# 빌드 소스·임시 ECR 인증 응답을 성공/실패 모두 정리한다.
trap 'if [[ -n "$WORK_DIR" ]]; then rm -rf -- "$WORK_DIR"; fi' EXIT

case "${1:-}" in
  "") ;;
  --bootstrap) BOOTSTRAP=true ;;
  *) die '사용법: sudo bash install-worker-ecr.sh' ;;
esac
[[ "$(id -u)" == 0 ]] || die '워커에서 sudo bash로 실행하세요.'
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || die '현재 스크립트는 Ubuntu Linux amd64 워커용입니다.'
# CP의 kubelet을 잘못 바꾸지 않도록 파일로 구분한다.
if [[ -f /etc/kubernetes/admin.conf || -f /etc/kubernetes/manifests/kube-apiserver.yaml ]]; then
  die '이 서버는 Control Plane입니다. CP1이 아니라 워커에 접속해서 실행하세요.'
fi
if [[ "$BOOTSTRAP" == false && ! -f /etc/kubernetes/kubelet.conf ]]; then
  die '아직 가입하지 않은 서버입니다. 기존 워커의 가입부터 완료하세요.'
fi
# 같은 워커에서 중복 실행해 컴파일·재시작이 겹치는 것을 막는다.
exec 9>/run/lock/mulo-worker-ecr.lock
flock -n 9 || die '다른 터미널에서 이 워커의 설치를 진행 중입니다.'
command -v kubelet >/dev/null || die 'kubelet이 설치된 워커에서 실행하세요.'
command -v apt-get >/dev/null || die 'Ubuntu apt 패키지 환경이 필요합니다.'

# 제공된 표준 kubeadm 시작 옵션을 모두 보존할 수 있는지 먼저 확인한다.
BASE_EXEC='ExecStart=/usr/bin/kubelet $KUBELET_KUBECONFIG_ARGS $KUBELET_CONFIG_ARGS $KUBELET_KUBEADM_ARGS $KUBELET_EXTRA_ARGS'
CURRENT_EXEC="$(systemctl cat kubelet | sed -n '/^ExecStart=./p' | tail -n 1)"
if [[ "$CURRENT_EXEC" != "$BASE_EXEC" && "$CURRENT_EXEC" != "$BASE_EXEC \$KUBELET_ECR_ARGS" ]]; then
  die '기존 kubelet 시작 명령이 표준 kubeadm 구성과 다릅니다. 기존 설정을 보존하기 위해 중단합니다.'
fi

WORK_DIR="$(mktemp -d /tmp/mulo-worker-ecr.XXXXXX)"
log "워커에서 ECR 인증을 설정합니다: $(hostname)"
STAGE="필요한 패키지 확인"
PACKAGES=()
command -v jq >/dev/null || PACKAGES+=(jq)
[[ -s /etc/ssl/certs/ca-certificates.crt ]] || PACKAGES+=(ca-certificates)

# 설치된 실행 파일이 있으면 Go 설치와 컴파일을 반복하지 않는다.
NEED_BUILD=false
GO_BIN=""
go_supported() {
  local version
  version="$("$1" version 2>/dev/null)" || return 1
  [[ "$version" =~ go([0-9]+)\.([0-9]+) ]] || return 1
  (( BASH_REMATCH[1] > 1 || (BASH_REMATCH[1] == 1 && BASH_REMATCH[2] >= 26) ))
}
if [[ ! -x "$PROVIDER" ]]; then
  NEED_BUILD=true
  command -v git >/dev/null || PACKAGES+=(git)
  if command -v go >/dev/null && go_supported "$(command -v go)"; then
    GO_BIN="$(command -v go)"
  else
    PACKAGES+=(golang-go)
  fi
fi
if [[ -n "${PACKAGES[*]:-}" ]]; then
  log "없는 도구를 설치합니다: ${PACKAGES[*]}"
  apt-get -o DPkg::Lock::Timeout=120 update
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 \
    install -y --no-install-recommends "${PACKAGES[@]}"
fi

if [[ "$NEED_BUILD" == true ]]; then
  STAGE="ECR 인증 프로그램 빌드"
  if [[ -z "$GO_BIN" ]]; then
    GO_BIN=/usr/bin/go
  fi
  go_supported "$GO_BIN" || die 'Go 1.26 이상이 필요합니다. go version 출력을 확인하세요.'
  log '실행 파일을 빌드합니다. 처음에는 다운로드·컴파일에 몇 분이 걸릴 수 있습니다.'
  git clone --quiet --depth 1 --branch "$VERSION" \
    https://github.com/kubernetes/cloud-provider-aws.git "$WORK_DIR/source"
  [[ "$(git -C "$WORK_DIR/source" rev-parse HEAD)" == "$SOURCE_COMMIT" ]] || die '받은 소스가 확인된 릴리스 커밋과 다릅니다.'
  (
    cd "$WORK_DIR/source"
    # 한 번에 컴파일 하나만 실행해 워커의 CPU·메모리 사용을 줄인다.
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOMAXPROCS=1 GOTOOLCHAIN=local \
      GOCACHE="$WORK_DIR/go-cache" GOMODCACHE="$WORK_DIR/go-modules" \
      "$GO_BIN" build -p 1 -trimpath -o "$WORK_DIR/ecr-credential-provider" ./cmd/ecr-credential-provider
  )
  CANDIDATE="$WORK_DIR/ecr-credential-provider"
else
  log '이미 설치된 실행 파일을 재사용합니다.'
  CANDIDATE="$PROVIDER"
fi

STAGE="EC2 IAM 역할로 ECR 인증 확인"
# 인증 비밀번호는 root 전용 임시 파일에만 쓰고 화면/Git에는 남기지 않는다.
printf '{"apiVersion":"credentialprovider.kubelet.k8s.io/v1","kind":"CredentialProviderRequest","image":"%s/mulo-v3-prod/frontend:1.17.0"}\n' \
  "$REGISTRY" > "$WORK_DIR/request.json"
# 현재 터미널의 AWS 키/프로필 대신 실제 EC2 인스턴스 IAM 역할로 검사한다.
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
  -u AWS_PROFILE -u AWS_ROLE_ARN -u AWS_WEB_IDENTITY_TOKEN_FILE \
  -u AWS_CONTAINER_CREDENTIALS_RELATIVE_URI -u AWS_CONTAINER_CREDENTIALS_FULL_URI \
  AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_CONFIG_FILE=/dev/null \
  AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" AWS_EC2_METADATA_DISABLED=false \
  timeout 60s "$CANDIDATE" < "$WORK_DIR/request.json" > "$WORK_DIR/response.json"
jq -e '(.kind=="CredentialProviderResponse") and
  ((.auth // {})|length>0) and
  all(.auth[]; (.username|length>0) and (.password|length>0))' \
  "$WORK_DIR/response.json" >/dev/null
log 'EC2 IAM 역할의 ECR 인증을 확인했습니다. 인증값은 출력하지 않습니다.'

STAGE="kubelet 설정 저장"
# kubeadm의 workload·taint·provider-id와 기존 EXTRA_ARGS는 변경하지 않는다.
cat > "$WORK_DIR/config" <<YAML
apiVersion: kubelet.config.k8s.io/v1
kind: CredentialProviderConfig
providers:
  - name: ecr-credential-provider
    matchImages:
      - "$REGISTRY"
    defaultCacheDuration: "12h"
    apiVersion: credentialprovider.kubelet.k8s.io/v1
YAML
cat > "$WORK_DIR/dropin" <<'UNIT'
[Service]
Environment="KUBELET_ECR_ARGS=--image-credential-provider-bin-dir=/opt/kubelet-credential-providers --image-credential-provider-config=/etc/kubernetes/ecr-credential-provider.yaml"
ExecStart=
ExecStart=/usr/bin/kubelet $KUBELET_KUBECONFIG_ARGS $KUBELET_CONFIG_ARGS $KUBELET_KUBEADM_ARGS $KUBELET_EXTRA_ARGS $KUBELET_ECR_ARGS
UNIT
install -d -m 0755 "$BIN_DIR" /etc/kubernetes /etc/systemd/system/kubelet.service.d
if [[ "$NEED_BUILD" == true ]]; then
  install -m 0755 "$CANDIDATE" "$PROVIDER"
fi

# 설정이 바뀔 때만 원본을 백업한다. 재실행할 때 불필요한 백업을 늘리지 않는다.
if ! cmp -s "$WORK_DIR/config" "$CONFIG" || ! cmp -s "$WORK_DIR/dropin" "$DROPIN"; then
  install -d -m 0700 "$BACKUP_ROOT"
  BACKUP_DIR="$(mktemp -d "$BACKUP_ROOT/before.XXXXXX")"
  [[ ! -f "$CONFIG" ]] || cp -p "$CONFIG" "$BACKUP_DIR/config"
  [[ ! -f "$DROPIN" ]] || cp -p "$DROPIN" "$BACKUP_DIR/dropin"
  CONFIG_CHANGED=true
  install -m 0644 "$WORK_DIR/config" "$CONFIG"
  install -m 0644 "$WORK_DIR/dropin" "$DROPIN"
fi

STAGE="systemd 설정 다시 읽기"
systemctl daemon-reload
if [[ "$BOOTSTRAP" == true ]]; then
  # ASG에서는 다음 kubeadm join이 kubelet을 시작한다. 가입 전에 재시작하지 않는다.
  log 'ASG 가입 전 ECR 준비 완료. 이어서 자동 join을 진행합니다.'
  exit 0
fi

STAGE="kubelet 시작 옵션 적용"
ARGS="$(ps -ww -C kubelet -o args= || true)"
if [[ "$CONFIG_CHANGED" == true ]] || ! systemctl is-active --quiet kubelet ||
   [[ "$ARGS" != *"--image-credential-provider-bin-dir=$BIN_DIR"* || "$ARGS" != *"--image-credential-provider-config=$CONFIG"* ]]; then
  RESTART_ATTEMPTED=true
  systemctl restart kubelet
fi
# 화면 폭에 잘리는 ps 기본 출력을 사용하지 않고 실제 옵션을 검사한다.
for ((attempt=1; attempt<=30; attempt++)); do
  ARGS="$(ps -ww -C kubelet -o args= || true)"
  if systemctl is-active --quiet kubelet &&
     [[ "$ARGS" == *"--image-credential-provider-bin-dir=$BIN_DIR"* && "$ARGS" == *"--image-credential-provider-config=$CONFIG"* ]]; then
    log '완료: kubelet active / ECR 인증 성공 / 시작 옵션 2개 적용'
    log 'CP1에서 kubectl get nodes -o wide -L workload로 Ready 상태를 확인하세요.'
    exit 0
  fi
  sleep 1
done
# 수동 exit 대신 ERR 처리로 연결해 변경한 설정을 복구한다.
log 'kubelet 시작 옵션을 확인하지 못했습니다.' >&2
false

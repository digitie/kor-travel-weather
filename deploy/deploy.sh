#!/usr/bin/env bash
#
# Build and start the stack, then prove the running API is the commit you meant.
#
# Two things went wrong deploying by hand, and this exists to make both
# impossible rather than documented:
#
#   * `docker compose up -d --build` uses compose.yaml alone, which publishes
#     every port on loopback. n150's HAProxy runs outside Docker and dials the
#     LAN address, so that deploy answered 503 on all three public hostnames
#     while every container reported healthy. deploy/compose.n150.yaml has bound
#     those ports to the LAN since "expose n150 app ports to LAN gateway", and
#     the deploy simply did not pass it. Set COMPOSE_FILE in .env (see
#     .env.example) so every compose command in the directory picks it up --
#     including the ones run by hand in a hurry.
#
#   * Omitting GIT_COMMIT bakes "unknown" into the image, and then /version
#     answers 200 with a value that cannot disagree with anything. The smoke
#     test passes and confirms nothing.
#
# Usage:  deploy/deploy.sh [compose service ...]
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "deploy.sh must run inside the git checkout: /version needs a commit to report" >&2
    exit 1
fi

GIT_COMMIT="$(git describe --always --dirty --abbrev=7)"
export GIT_COMMIT

case "$GIT_COMMIT" in
    *-dirty)
        # Not fatal: a hotfix edited in place is a real thing to deploy. But the
        # suffix is the only warning that the image matches no commit at all.
        echo "warning: checkout has uncommitted changes, deploying as $GIT_COMMIT" >&2
        ;;
esac

# Compose reads COMPOSE_FILE from .env itself, so it is usually not in this
# shell's environment. Reporting the shell's view said "using compose.yaml" on a
# deploy that in fact used the n150 override -- a deploy script claiming the
# wrong configuration is the same failure this script exists to prevent.
compose_files="${COMPOSE_FILE:-}"
if [ -z "$compose_files" ]; then
    compose_files="$(grep -E '^COMPOSE_FILE=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
fi
echo "deploying $GIT_COMMIT using ${compose_files:-compose.yaml}"

# Detached, so losing the SSH connection cannot kill a build midway through.
nohup docker compose up -d --build "$@" > /tmp/kor-travel-weather-deploy.log 2>&1 &
build_pid=$!
echo "build running (pid $build_pid); log: /tmp/kor-travel-weather-deploy.log"

build_failed() {
    echo "build failed; see /tmp/kor-travel-weather-deploy.log" >&2
    tail -30 /tmp/kor-travel-weather-deploy.log >&2
    exit 1
}

# Ask compose where it published the API rather than assuming. Checking
# 127.0.0.1 on a host that publishes to its LAN address reports a healthy
# service as down; checking a remembered address reports a service that moved as
# healthy. Either way the check is about the wrong process.
deadline=$(( $(date +%s) + 1800 ))
published=""
while [ -z "$published" ]; do
    published="$(docker compose port api 14101 2>/dev/null || true)"
    if [ -z "$published" ]; then
        kill -0 "$build_pid" 2>/dev/null || wait "$build_pid" || build_failed
        [ "$(date +%s)" -lt "$deadline" ] || {
            echo "timed out waiting for the api container to publish a port" >&2
            exit 1
        }
        sleep 10
    fi
done
# 0.0.0.0 is a bind address, not somewhere to connect to.
probe="${published/#0.0.0.0:/127.0.0.1:}"
echo "verifying http://${probe}/version"

until curl -sf --max-time 5 "http://${probe}/version" 2>/dev/null \
    | grep -q "\"git_commit\":\"${GIT_COMMIT}\""; do
    kill -0 "$build_pid" 2>/dev/null || wait "$build_pid" || build_failed
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "timed out waiting for ${probe}/version to report ${GIT_COMMIT}" >&2
        curl -s --max-time 5 "http://${probe}/version" >&2 || true
        echo >&2
        exit 1
    fi
    sleep 10
done

echo "deployed: /version reports ${GIT_COMMIT}"
curl -s --max-time 5 "http://${probe}/version"
echo

# 시작 warmup(/ready)을 최대 60초 확인한다. 끝나지 않아도 배포를 실패로 만들지 않는다:
# warmup은 첫 관리 지도 요청을 빠르게 할 뿐이고, 트래픽을 막을지는 HAProxy/Manager가 정한다.
# /ready는 warmup이 끝났거나(done) 예산 초과·사용자 요청 겹침으로 생략됐거나(skipped)
# 꺼져 있으면(disabled) 200, 진행 중이면 503이다. 단계별 결과와 시간은 본문의 steps에 있고,
# 마커 단계는 데운 위치 수, 상세 단계는 마친 하위 읽기 수(warmed/planned)도 담는다.
ready_deadline=$(( $(date +%s) + 60 ))
ready_status=""
ready_body=""
while :; do
    ready_reply="$(curl -s --max-time 5 -w '\n%{http_code}' "http://${probe}/ready" 2>/dev/null || true)"
    ready_status="${ready_reply##*$'\n'}"
    ready_body="${ready_reply%$'\n'*}"
    if [ "$ready_status" = "200" ] || [ "$(date +%s)" -ge "$ready_deadline" ]; then
        break
    fi
    sleep 2
done
if [ "$ready_status" = "200" ]; then
    echo "ready: ${ready_body}"
    # /version·/health는 DB를 보지 않는다. 새 배포의 DB 도달 여부는 이 본문이 유일한 신호라
    # 실패 단계나 첫 연결 실패(reason=error)는 stderr에 남긴다. 배포는 그대로 성공이다.
    case "$ready_body" in
        *'"reason":"error"'* | *'"outcome":"failed"'*)
            echo "warning: /ready reports a failed startup warmup step" \
                "(the API may not reach the database); the deploy itself succeeded" >&2
            ;;
    esac
else
    echo "warning: /ready did not answer 200 within 60s (last status ${ready_status:-none});" \
        "the deploy itself succeeded, the first admin map requests may be slower" >&2
    if [ -n "$ready_body" ]; then
        echo "$ready_body" >&2
    fi
fi

# Map 표준 프로필(current limit 2000) export 페이지 사전 구성을 한 번 돌린다(호출 검토 A3).
# 새 API가 같은 설정(활성 provider·artifact root)으로 만들도록 api 컨테이너에서 실행한다.
# 입력(승인 inventory·catalog·삭제 epoch)이 마지막 build와 같으면 아무 것도 쓰지 않고(unchanged),
# export나 사전 구성이 꺼져 있으면 disabled로 끝난다. 실패·5분 초과는 경고만 한다: 사전 구성은
# Map 첫 페이지를 빠르게 할 뿐이고, 없거나 입력이 바뀌었으면 API가 기존 freeze 경로를 쓴다.
# 호스트의 timeout은 docker exec client만 끝내고 컨테이너 안 프로세스는 남는다. 그래서 컨테이너
# 안에서도 timeout(280초)으로 builder 프로세스를 끊고, builder 자체 예산(240초)은 그보다 짧게 준다.
# 끊겨도 저장은 한 transaction이라 반쯤 쓴 build는 남지 않는다.
if prebuild_reply="$(timeout 300 docker compose exec -T api \
        timeout 280 python -m kortravelweather.export_prebuilt --budget-seconds 240 \
        2>&1 | tail -1)"; then
    echo "export prebuild: ${prebuild_reply}"
else
    echo "warning: export prebuild did not finish (${prebuild_reply:-no output});" \
        "the deploy itself succeeded, Map's first export page uses the regular path" >&2
fi
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

echo "deploying $GIT_COMMIT using ${COMPOSE_FILE:-compose.yaml}"

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

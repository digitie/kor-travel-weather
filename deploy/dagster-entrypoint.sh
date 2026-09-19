#!/bin/sh
#
# `dagster dev` bundles the webserver, daemon and code-server in one process.
# Its code-server child shuts itself down if it misses a heartbeat -- observed
# under host CPU/memory contention on a machine shared with several other
# projects' Dagster daemons -- and `dagster dev`'s own recovery can then get
# stuck permanently reporting "Error loading repository location" without the
# top-level process ever exiting. `restart: unless-stopped` never fires in
# that state, because nothing has actually crashed from Docker's point of
# view: confirmed once in production, stuck for about 8 hours until noticed
# by hand, and every job appeared to fail because none could be launched.
#
# This wrapper is the missing supervision. On a failed check it first asks
# the still-alive outer proxy to reload the code location (the same action
# the UI's own "Reload" button sends, dagster_reload_location.py) -- tested
# against a deliberately killed inner worker, this alone fixes it in a few
# seconds with no restart at all, because dagster dev's own recovery loop
# only watches the outer proxy's liveness and never notices the inner worker
# died on its own (see dagster_reload_location.py for the sourced reasoning).
# Only if the workspace stays unhealthy for ten straight minutes even with
# reload attempts does it fall back to killing `dagster dev` so the container
# actually exits and the existing restart policy takes over.
set -eu

dagster dev -m kortravelweather_dagster.definitions -h 0.0.0.0 -p 14102 &
dagster_pid=$!

# SIGTERM, then SIGKILL if it hasn't exited within the grace period -- tested
# against a genuinely wedged dagster dev, a plain SIGTERM alone left it
# running for 30+ minutes, so a graceful exit cannot be trusted to ever
# happen.
terminate() {
    kill -TERM "$dagster_pid" 2>/dev/null || return 0
    waited=0
    while kill -0 "$dagster_pid" 2>/dev/null && [ "$waited" -lt 20 ]; do
        sleep 1
        waited=$((waited + 1))
    done
    if kill -0 "$dagster_pid" 2>/dev/null; then
        echo "dagster-entrypoint: dagster dev did not exit within ${waited}s of SIGTERM, sending SIGKILL" >&2
        kill -KILL "$dagster_pid" 2>/dev/null || true
    fi
}

# PID 1 in a container does not get the kernel's default action for an
# unhandled signal -- without this trap, SIGTERM from `docker stop` or
# `compose down` would be silently ignored and every shutdown would burn its
# full grace period waiting on the eventual SIGKILL, instead of stopping
# promptly like the plain `dagster dev` command this replaces.
trap 'terminate; exit 143' TERM INT

# A single long `sleep N` would defer that trap for up to N seconds: tested
# against both bash and dash, neither runs a pending trap while blocked
# inside a foreground command, only between commands. Sleeping in one-second
# steps is what lets the trap actually run within about a second of the
# signal arriving.
wait_seconds() {
    remaining=$1
    while [ "$remaining" -gt 0 ]; do
        sleep 1
        remaining=$((remaining - 1))
    done
}

# Give the server time to finish its first boot before the first check --
# well above what a healthy startup needs, so this cannot itself flap.
wait_seconds 60

failures=0
while kill -0 "$dagster_pid" 2>/dev/null; do
    if python /app/deploy/dagster_healthcheck.py; then
        failures=0
    else
        echo "dagster-entrypoint: workspace health check failed, asking dagster dev to reload the code location" >&2
        python /app/deploy/dagster_reload_location.py || true
        recovered=0
        attempt=0
        while [ "$attempt" -lt 3 ]; do
            wait_seconds 5
            if python /app/deploy/dagster_healthcheck.py; then
                recovered=1
                break
            fi
            attempt=$((attempt + 1))
        done
        if [ "$recovered" -eq 1 ]; then
            echo "dagster-entrypoint: reload fixed the code location, no restart needed" >&2
            failures=0
        else
            failures=$((failures + 1))
            echo "dagster-entrypoint: still unhealthy after a reload attempt (${failures} consecutive)" >&2
            if [ "$failures" -ge 10 ]; then
                echo "dagster-entrypoint: workspace unhealthy for ${failures} consecutive minutes even with reload attempts, restarting" >&2
                terminate
                wait "$dagster_pid" 2>/dev/null || true
                exit 1
            fi
        fi
    fi
    wait_seconds 60
done

# dagster dev exited on its own (a real crash, not a wedged code-server) --
# propagate its exit code so `restart: unless-stopped` reacts the same way it
# always has.
wait "$dagster_pid"

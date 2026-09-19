"""Ask the (still-alive) dagster dev proxy to relaunch its inner code-server worker.

The code-server child that actually imports our module can die (missed
heartbeat, see dagster_healthcheck.py) while the outer proxy dagster dev
manages stays up. dagster dev's own monitoring only watches that outer
proxy's liveness, so it never notices the inner worker is gone and never
re-issues the reload that would fix it on its own -- confirmed against
dagster 1.13's source (dagster/_cli/proxy_server_manager.py) and a matching
open upstream issue (dagster-io/dagster#24050).

The `reloadRepositoryLocation` GraphQL mutation is the same "Reload" action
the Dagster UI's own button sends, and it reaches the outer proxy directly
(which is still alive and listening) rather than the dead inner worker, so it
works even while the location is reporting broken. Tested against a
deliberately killed inner worker: the first call can still return the old
broken state (it appears to kick off the relaunch without waiting for it),
but a follow-up healthcheck a few seconds later reliably reports healthy --
this script only fires the mutation, the caller (dagster-entrypoint.sh)
decides how long to wait before re-checking.
"""

import json
import sys
import urllib.error
import urllib.request

LOCATION_NAME = "kortravelweather_dagster.definitions"

MUTATION = """
mutation($name: String!) {
  reloadRepositoryLocation(repositoryLocationName: $name) {
    __typename
    ... on WorkspaceLocationEntry {
      name
      loadStatus
      locationOrLoadError {
        __typename
        ... on PythonError { message }
      }
    }
    ... on ReloadNotSupported { message }
    ... on RepositoryLocationNotFound { message }
    ... on PythonError { message }
  }
}
"""


def main() -> int:
    body = json.dumps({"query": MUTATION, "variables": {"name": LOCATION_NAME}}).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:14102/graphql",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"dagster_reload_location: request failed: {exc}", file=sys.stderr)
        return 1

    result = payload.get("data", {}).get("reloadRepositoryLocation", {})
    print(f"dagster_reload_location: {result}", file=sys.stderr)
    # Deliberately not treated as success/failure here: the first response can
    # still show the stale broken state even when the relaunch it triggered
    # goes on to succeed a few seconds later. The caller re-checks separately.
    return 0


if __name__ == "__main__":
    sys.exit(main())

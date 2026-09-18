"""Exit 0 only if every Dagster workspace location is loaded without error.

``dagster dev`` can report itself as running (the process is alive, the port
is open) while its own code-server child is dead -- the webserver just keeps
answering "Error loading repository location" instead. A plain port/HTTP
check cannot tell the two apart; this asks the workspace directly.
"""

import json
import sys
import urllib.error
import urllib.request

QUERY = """
{
  workspaceOrError {
    __typename
    ... on Workspace {
      locationEntries {
        name
        loadStatus
        locationOrLoadError {
          __typename
          ... on PythonError { message }
        }
      }
    }
    ... on PythonError { message }
  }
}
"""


def main() -> int:
    body = json.dumps({"query": QUERY}).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:14102/graphql",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"dagster_healthcheck: request failed: {exc}", file=sys.stderr)
        return 1

    workspace = payload.get("data", {}).get("workspaceOrError", {})
    if workspace.get("__typename") != "Workspace":
        print(f"dagster_healthcheck: workspace error: {workspace}", file=sys.stderr)
        return 1

    entries = workspace.get("locationEntries", [])
    if not entries:
        print("dagster_healthcheck: no locations registered", file=sys.stderr)
        return 1

    for entry in entries:
        location = entry.get("locationOrLoadError") or {}
        healthy = (
            entry.get("loadStatus") == "LOADED"
            and location.get("__typename") == "RepositoryLocation"
        )
        if not healthy:
            print(f"dagster_healthcheck: location unhealthy: {entry}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

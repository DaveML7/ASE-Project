"""Generate the Postman/Bruno collections in docs/postman/.

    python tests/api/build_collections.py        # rewrites docs/postman/*.json

The collections are plain Postman Collection v2.1 JSON: importable in Postman and in Bruno,
and runnable headless with Newman. One module per service lives in tests/api/suites; the same
requests run twice (component tests against each service, integration tests through the
gateway) because they only use `{{<service>_url}}`, never a hard-coded host.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import suites.acceptance  # noqa: E402
import suites.auth  # noqa: E402
import suites.encounters  # noqa: E402
import suites.history  # noqa: E402
import suites.monsters  # noqa: E402
import suites.players  # noqa: E402
import suites.teams  # noqa: E402
from suites.dsl import OUT, SERVICES  # noqa: E402


def environment(name, urls):
    values = [
        {"key": f"{service}_url", "value": urls[service], "enabled": True} for service in SERVICES
    ]
    return {"id": name, "name": name, "values": values, "_postman_variable_scope": "environment"}


def write(path: pathlib.Path, data: dict):
    path.write_text(json.dumps(data, indent=2) + "\n")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    builders = {
        "auth": suites.auth.auth_collection,
        "players": suites.players.players_collection,
        "monsters": suites.monsters.monsters_collection,
        "teams": suites.teams.teams_collection,
        "encounters": suites.encounters.encounters_collection,
        "history": suites.history.history_collection,
        "acceptance": suites.acceptance.acceptance_collection,
    }
    for name, build in builders.items():
        write(OUT / f"{name}.postman_collection.json", build())

    write(
        OUT / "component.postman_environment.json",
        environment("component", {s: f"https://localhost:{p}" for s, p in SERVICES.items()}),
    )
    gateway = "https://localhost:8443/api/v1"
    write(
        OUT / "integration.postman_environment.json",
        environment("integration", {s: gateway for s in SERVICES}),
    )


if __name__ == "__main__":
    main()

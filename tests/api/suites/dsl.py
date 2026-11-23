"""Tiny DSL that builds Postman requests, tests and folders (shared by every collection module).

The collections are generated into docs/postman/ by tests/api/build_collections.py.

The collections are plain Postman Collection v2.1 JSON: importable in Postman
and in Bruno ("Import collection > Postman"), and runnable headless with Newman.

    python tests/api/build_collections.py        # rewrites docs/postman/*.json

One collection per microservice, each with a valid-input and an invalid-input
request for every endpoint. The same requests run twice:

* component tests   -> environment "component": every service is called on its own port
* integration tests -> environment "integration": everything goes through the gateway

so a request only ever uses `{{<service>_url}}`, never a hard-coded host.
"""

import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "postman"
SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
PASSWORD = "Passw0rd!x"

SERVICES = {
    "auth": 5001,
    "players": 5002,
    "monsters": 5003,
    "teams": 5004,
    "encounters": 5005,
    "history": 5006,
}


# --------------------------------------------------------------------------- helpers
def check(label: str, body: str) -> str:
    return f"pm.test({json.dumps(label)}, function () {{ {body} }});"


def status(code: int) -> str:
    return check(f"status is {code}", f"pm.response.to.have.status({code});")


def body_check(label: str, expression: str) -> str:
    return check(label, f"const b = pm.response.json(); {expression}")


def save(variable: str, expression: str) -> str:
    return f"pm.collectionVariables.set({json.dumps(variable)}, {expression});"


def request(
    name,
    method,
    service,
    path,
    *,
    token=None,
    service_token=None,
    body=None,
    tests=(),
    pre=(),
    query=None,
):
    headers = []
    if body is not None:
        headers.append({"key": "Content-Type", "value": "application/json"})
    if service_token:
        headers.append({"key": "X-Service-Token", "value": service_token})
    base = "{{" + service + "_url}}"
    req = {
        "method": method,
        "header": headers,
        "url": {
            "raw": base
            + path
            + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else ""),
            "host": [base],
            "path": [segment for segment in path.split("/") if segment],
        },
    }
    if query:
        req["url"]["query"] = [{"key": k, "value": str(v)} for k, v in query.items()]
    if token:
        # "!text" sends the literal text (a forged token); anything else is a variable name.
        value = token[1:] if token.startswith("!") else "{{" + token + "}}"
        req["auth"] = {
            "type": "bearer",
            "bearer": [{"key": "token", "value": value, "type": "string"}],
        }
    if body is not None:
        raw = body if isinstance(body, str) else json.dumps(body)
        req["body"] = {"mode": "raw", "raw": raw, "options": {"raw": {"language": "json"}}}
    events = []
    if pre:
        events.append(
            {"listen": "prerequest", "script": {"type": "text/javascript", "exec": list(pre)}}
        )
    events.append({"listen": "test", "script": {"type": "text/javascript", "exec": list(tests)}})
    return {"name": name, "event": events, "request": req}


def folder(name, items):
    return {"name": name, "item": items}


def collection(name, description, folders):
    return {
        "info": {"name": name, "description": description, "schema": SCHEMA},
        "item": folders,
        "variable": [],
    }


def new_username(variable):
    return [
        save(
            variable,
            "'u' + Date.now().toString(36) + Math.floor(Math.random() * 1e6).toString(36)",
        )
    ]


# --------------------------------------------------------------------------- setup steps
def register(label, user_var, token_var, id_var=None):
    tests = [status(201), save(token_var, "pm.response.json().access_token")]
    if id_var:
        tests.append(save(id_var, "pm.response.json().player_id"))
    return request(
        f"setup: register player {label}",
        "POST",
        "auth",
        "/auth/register",
        body={"username": "{{" + user_var + "}}", "password": PASSWORD},
        pre=new_username(user_var),
        tests=tests,
    )


def read_collection(label, token_var, prefix):
    return request(
        f"setup: read collection of player {label}",
        "GET",
        "players",
        "/players/me/monsters",
        token=token_var,
        tests=[
            status(200),
            body_check("starter collection has 3 monsters", "pm.expect(b.length).to.equal(3);"),
            save(prefix + "1", "pm.response.json()[0].player_monster_id"),
            save(prefix + "2", "pm.response.json()[1].player_monster_id"),
            save(prefix + "3", "pm.response.json()[2].player_monster_id"),
        ],
    )


def create_team(label, token_var, prefix, team_var):
    return request(
        f"setup: create team for player {label}",
        "POST",
        "teams",
        "/teams",
        token=token_var,
        body='{"name":"setup team","member_ids":[{{'
        + prefix
        + "1}},{{"
        + prefix
        + "2}},{{"
        + prefix
        + "3}}]}",
        tests=[status(201), save(team_var, "pm.response.json().id")],
    )


def no_token(name, method, service, path, body=None):
    return request(
        name,
        method,
        service,
        path,
        body=body,
        tests=[
            status(401),
            body_check("error is reported", "pm.expect(b.error).to.be.a('string');"),
        ],
    )


# --------------------------------------------------------------------------- collections

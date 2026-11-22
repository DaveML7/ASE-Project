"""Generate the Postman/Bruno collections in docs/postman/.

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
def auth_collection():
    public = [
        request(
            "register - valid input",
            "POST",
            "auth",
            "/auth/register",
            body={"username": "{{user}}", "password": PASSWORD},
            pre=new_username("user"),
            tests=[
                status(201),
                body_check(
                    "returns a bearer token and the player id",
                    "pm.expect(b.access_token).to.be.a('string'); pm.expect(b.token_type).to.equal('Bearer'); pm.expect(b.player_id).to.be.a('string');",
                ),
                check(
                    "password is never echoed",
                    "pm.expect(pm.response.text()).to.not.include('" + PASSWORD + "');",
                ),
                save("token", "pm.response.json().access_token"),
            ],
        ),
        request(
            "register - username already taken",
            "POST",
            "auth",
            "/auth/register",
            body={"username": "{{user}}", "password": PASSWORD},
            tests=[status(409)],
        ),
        request(
            "register - invalid username",
            "POST",
            "auth",
            "/auth/register",
            body={"username": "not valid!", "password": PASSWORD},
            tests=[status(400)],
        ),
        request(
            "register - password too short",
            "POST",
            "auth",
            "/auth/register",
            body={"username": "shortpw_user", "password": "short"},
            tests=[status(400)],
        ),
        request(
            "register - empty body", "POST", "auth", "/auth/register", body={}, tests=[status(400)]
        ),
        request(
            "register - body is not a JSON object",
            "POST",
            "auth",
            "/auth/register",
            body="[1]",
            tests=[status(400)],
        ),
        request(
            "register - password is not a string",
            "POST",
            "auth",
            "/auth/register",
            body={"username": "valid_user_1", "password": ["longenough1"]},
            tests=[status(400)],
        ),
        request(
            "register - password too long",
            "POST",
            "auth",
            "/auth/register",
            body={"username": "valid_user_2", "password": "x" * 129},
            tests=[status(400)],
        ),
        request(
            "login - valid credentials",
            "POST",
            "auth",
            "/auth/login",
            body={"username": "{{user}}", "password": PASSWORD},
            tests=[
                status(200),
                body_check(
                    "returns a bearer token",
                    "pm.expect(b.access_token).to.be.a('string'); pm.expect(b.expires_in).to.be.above(0);",
                ),
                save("token", "pm.response.json().access_token"),
            ],
        ),
        request(
            "login - wrong password",
            "POST",
            "auth",
            "/auth/login",
            body={"username": "{{user}}", "password": "WrongPassw0rd"},
            tests=[status(401)],
        ),
        request(
            "login - unknown user",
            "POST",
            "auth",
            "/auth/login",
            body={"username": "nobody_here_1", "password": PASSWORD},
            tests=[status(401)],
        ),
        request(
            "login - injection attempt is just a wrong login",
            "POST",
            "auth",
            "/auth/login",
            body={"username": "' OR '1'='1", "password": "' OR '1'='1"},
            tests=[status(401)],
        ),
        request(
            "me - valid token",
            "GET",
            "auth",
            "/auth/me",
            token="token",
            tests=[
                status(200),
                body_check(
                    "identifies the caller",
                    "pm.expect(b.username).to.equal(pm.collectionVariables.get('user'));",
                ),
            ],
        ),
        no_token("me - missing token", "GET", "auth", "/auth/me"),
        no_token("logout - missing token", "POST", "auth", "/auth/logout"),
        request(
            "logout - valid token",
            "POST",
            "auth",
            "/auth/logout",
            token="token",
            tests=[status(204)],
        ),
        request(
            "logout - repeating it is harmless",
            "POST",
            "auth",
            "/auth/logout",
            token="token",
            tests=[
                check(
                    "accepted or already revoked",
                    "pm.expect([204, 401]).to.include(pm.response.code);",
                )
            ],
        ),
        request(
            "me - malformed token",
            "GET",
            "auth",
            "/auth/me",
            token="!not.a.token",
            tests=[status(401)],
        ),
    ]
    return collection(
        "ASE - auth",
        "Component tests for the auth service: registration, login and token validation.",
        [folder("Public API", public)],
    )


def players_collection():
    setup = [register("A", "userA", "tokenA", "playerA")]
    public = [
        request(
            "profile - valid token",
            "GET",
            "players",
            "/players/me",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "new player starts at level 1 with 0 xp and 3 monsters",
                    "pm.expect(b.level).to.equal(1); pm.expect(b.xp).to.equal(0); pm.expect(b.monsters_owned).to.equal(3);",
                ),
            ],
        ),
        no_token("profile - missing token", "GET", "players", "/players/me"),
        request(
            "profile - update avatar and faction",
            "PATCH",
            "players",
            "/players/me",
            token="tokenA",
            body={"avatar": "wizard", "faction": "Embers"},
            tests=[
                status(200),
                body_check(
                    "changes are returned",
                    "pm.expect(b.avatar).to.equal('wizard'); pm.expect(b.faction).to.equal('Embers');",
                ),
            ],
        ),
        request(
            "profile - update persists",
            "GET",
            "players",
            "/players/me",
            token="tokenA",
            tests=[
                status(200),
                body_check("avatar was saved", "pm.expect(b.avatar).to.equal('wizard');"),
            ],
        ),
        request(
            "profile - empty avatar",
            "PATCH",
            "players",
            "/players/me",
            token="tokenA",
            body={"avatar": ""},
            tests=[status(400)],
        ),
        request(
            "profile - avatar too long",
            "PATCH",
            "players",
            "/players/me",
            token="tokenA",
            body={"avatar": "x" * 41},
            tests=[status(400)],
        ),
        request(
            "profile - nothing to update",
            "PATCH",
            "players",
            "/players/me",
            token="tokenA",
            body={},
            tests=[status(400)],
        ),
        request(
            "profile - avatar is not a string",
            "PATCH",
            "players",
            "/players/me",
            token="tokenA",
            body={"avatar": ["wizard"]},
            tests=[status(400)],
        ),
        request(
            "profile - null faction clears it",
            "PATCH",
            "players",
            "/players/me",
            token="tokenA",
            body={"faction": None},
            tests=[
                status(200),
                body_check(
                    "faction is really null, not the text None",
                    "pm.expect(b.faction).to.equal(null);",
                ),
            ],
        ),
        no_token(
            "profile update - missing token",
            "PATCH",
            "players",
            "/players/me",
            body={"avatar": "x"},
        ),
        request(
            "collection - valid token",
            "GET",
            "players",
            "/players/me/monsters",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "3 starter monsters with stats and image",
                    "pm.expect(b).to.have.lengthOf(3); b.forEach(m => { pm.expect(m.player_monster_id).to.be.a('number'); pm.expect(m.element).to.be.a('string'); pm.expect(m.attack).to.be.a('number'); pm.expect(m.image_url).to.be.a('string'); });",
                ),
                save("monsterA", "pm.response.json()[0].player_monster_id"),
            ],
        ),
        no_token("collection - missing token", "GET", "players", "/players/me/monsters"),
    ]
    internal = [
        request(
            "internal collection - no service identity",
            "GET",
            "players",
            "/internal/players/{{playerA}}/monsters",
            token="tokenA",
            tests=[status(403)],
        ),
        request(
            "internal collection - forged service token",
            "GET",
            "players",
            "/internal/players/{{playerA}}/monsters",
            token="tokenA",
            service_token="forged.service.token",
            tests=[status(403)],
        ),
        request(
            "internal create player - no service identity",
            "POST",
            "players",
            "/internal/players",
            body={"player_id": "x", "username": "y"},
            tests=[status(403)],
        ),
    ]
    return collection(
        "ASE - players",
        "Component tests for the players service: profile, saved progress and personal collection.",
        [folder("Setup", setup), folder("Public API", public), folder("Internal API", internal)],
    )


def monsters_collection():
    public = [
        request(
            "catalogue - valid",
            "GET",
            "monsters",
            "/monsters",
            tests=[
                status(200),
                body_check(
                    "every monster has an image and two gameplay characteristics",
                    "pm.expect(b.length).to.be.at.least(10); b.forEach(m => { pm.expect(m.image_url).to.be.a('string'); pm.expect(m.element).to.be.a('string'); pm.expect(m.attack).to.be.a('number'); pm.expect(m.defence).to.be.a('number'); });",
                ),
            ],
        ),
        request(
            "catalogue - limit parameter",
            "GET",
            "monsters",
            "/monsters",
            query={"limit": 3},
            tests=[status(200), body_check("only 3 monsters", "pm.expect(b).to.have.lengthOf(3);")],
        ),
        request(
            "catalogue - invalid limit",
            "GET",
            "monsters",
            "/monsters",
            query={"limit": "abc"},
            tests=[status(400)],
        ),
        request(
            "catalogue - limit below 1",
            "GET",
            "monsters",
            "/monsters",
            query={"limit": 0},
            tests=[status(400)],
        ),
        request(
            "details - existing monster",
            "GET",
            "monsters",
            "/monsters/1",
            tests=[
                status(200),
                body_check(
                    "details are complete",
                    "pm.expect(b.id).to.equal(1); pm.expect(b.hp).to.be.a('number'); pm.expect(b.rarity).to.be.a('string');",
                ),
            ],
        ),
        request(
            "details - unknown monster", "GET", "monsters", "/monsters/999999", tests=[status(404)]
        ),
        request(
            "details - id is not a number", "GET", "monsters", "/monsters/abc", tests=[status(404)]
        ),
        request(
            "image - existing monster",
            "GET",
            "monsters",
            "/monsters/1/image",
            tests=[
                status(200),
                check(
                    "content type is SVG",
                    "pm.expect(pm.response.headers.get('Content-Type')).to.include('image/svg+xml');",
                ),
                check(
                    "artwork is cacheable by the browser",
                    "pm.expect(pm.response.headers.get('Cache-Control')).to.include('max-age');",
                ),
            ],
        ),
        request(
            "image - unknown monster",
            "GET",
            "monsters",
            "/monsters/999999/image",
            tests=[status(404)],
        ),
    ]
    return collection(
        "ASE - monsters",
        "Component tests for the monsters service: public catalogue, details and artwork.",
        [folder("Public API", public)],
    )


def teams_collection():
    setup = [
        register("A", "userA", "tokenA", "playerA"),
        read_collection("A", "tokenA", "pmA"),
        register("B", "userB", "tokenB"),
    ]
    public = [
        request(
            "create team - valid",
            "POST",
            "teams",
            "/teams",
            token="tokenA",
            body='{"name":"Alpha","member_ids":[{{pmA1}},{{pmA2}}]}',
            tests=[
                status(201),
                body_check(
                    "team stored with its members",
                    "pm.expect(b.name).to.equal('Alpha'); pm.expect(b.member_ids).to.have.lengthOf(2);",
                ),
                save("teamA", "pm.response.json().id"),
            ],
        ),
        request(
            "create team - monster not owned",
            "POST",
            "teams",
            "/teams",
            token="tokenA",
            body={"name": "Cheat", "member_ids": [999999]},
            tests=[
                status(422),
                body_check("violations explain why", "pm.expect(b.violations).to.not.be.empty;"),
            ],
        ),
        request(
            "create team - another player's monster",
            "POST",
            "teams",
            "/teams",
            token="tokenB",
            body='{"name":"Steal","member_ids":[{{pmA1}}]}',
            tests=[status(422)],
        ),
        request(
            "create team - duplicated monster",
            "POST",
            "teams",
            "/teams",
            token="tokenA",
            body='{"name":"Dup","member_ids":[{{pmA1}},{{pmA1}}]}',
            tests=[status(422)],
        ),
        request(
            "create team - too many monsters",
            "POST",
            "teams",
            "/teams",
            token="tokenA",
            body='{"name":"Big","member_ids":[{{pmA1}},{{pmA2}},{{pmA3}},999999]}',
            tests=[status(422)],
        ),
        request(
            "create team - empty team",
            "POST",
            "teams",
            "/teams",
            token="tokenA",
            body={"name": "Empty", "member_ids": []},
            tests=[status(422)],
        ),
        request(
            "create team - malformed body",
            "POST",
            "teams",
            "/teams",
            token="tokenA",
            body={"name": "Broken", "member_ids": "not-a-list"},
            tests=[status(400)],
        ),
        request(
            "create team - body is not a JSON object",
            "POST",
            "teams",
            "/teams",
            token="tokenA",
            body="[1]",
            tests=[status(400)],
        ),
        request(
            "create team - name is not a string",
            "POST",
            "teams",
            "/teams",
            token="tokenA",
            body='{"name":{"a":1},"member_ids":[{{pmA1}}]}',
            tests=[status(400)],
        ),
        no_token(
            "create team - missing token", "POST", "teams", "/teams", body={"member_ids": [1]}
        ),
        request(
            "list teams - valid",
            "GET",
            "teams",
            "/teams",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "contains the new team",
                    "pm.expect(b.map(t => t.id)).to.include(Number(pm.collectionVariables.get('teamA')));",
                ),
            ],
        ),
        request(
            "list teams - other player sees none of them",
            "GET",
            "teams",
            "/teams",
            token="tokenB",
            tests=[status(200), body_check("empty list", "pm.expect(b).to.have.lengthOf(0);")],
        ),
        no_token("list teams - missing token", "GET", "teams", "/teams"),
        request(
            "get team - valid",
            "GET",
            "teams",
            "/teams/{{teamA}}",
            token="tokenA",
            tests=[
                status(200),
                body_check("max size is exposed", "pm.expect(b.max_size).to.equal(3);"),
            ],
        ),
        request(
            "get team - unknown id",
            "GET",
            "teams",
            "/teams/999999",
            token="tokenA",
            tests=[status(404)],
        ),
        request(
            "get team - owned by another player",
            "GET",
            "teams",
            "/teams/{{teamA}}",
            token="tokenB",
            tests=[status(404)],
        ),
        request(
            "replace members - valid",
            "PUT",
            "teams",
            "/teams/{{teamA}}",
            token="tokenA",
            body='{"name":"Alpha 2","member_ids":[{{pmA3}}]}',
            tests=[
                status(200),
                body_check(
                    "only the new member remains",
                    "pm.expect(b.member_ids).to.eql([Number(pm.collectionVariables.get('pmA3'))]);",
                ),
            ],
        ),
        request(
            "replace members - monster not owned",
            "PUT",
            "teams",
            "/teams/{{teamA}}",
            token="tokenA",
            body={"member_ids": [999999]},
            tests=[status(422)],
        ),
        request(
            "replace members - malformed body",
            "PUT",
            "teams",
            "/teams/{{teamA}}",
            token="tokenA",
            body={"member_ids": 3},
            tests=[status(400)],
        ),
        request(
            "replace members - owned by another player",
            "PUT",
            "teams",
            "/teams/{{teamA}}",
            token="tokenB",
            body='{"member_ids":[{{pmA1}}]}',
            tests=[status(404)],
        ),
        request(
            "validate team - valid team",
            "POST",
            "teams",
            "/teams/{{teamA}}/validate",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "rules satisfied",
                    "pm.expect(b.valid).to.equal(true); pm.expect(b.violations).to.be.empty;",
                ),
            ],
        ),
        request(
            "validate team - unknown id",
            "POST",
            "teams",
            "/teams/999999/validate",
            token="tokenA",
            tests=[status(404)],
        ),
        request(
            "delete team - owned by another player",
            "DELETE",
            "teams",
            "/teams/{{teamA}}",
            token="tokenB",
            tests=[status(404)],
        ),
        request(
            "delete team - valid",
            "DELETE",
            "teams",
            "/teams/{{teamA}}",
            token="tokenA",
            tests=[status(204)],
        ),
        request(
            "delete team - already deleted",
            "DELETE",
            "teams",
            "/teams/{{teamA}}",
            token="tokenA",
            tests=[status(404)],
        ),
    ]
    internal = [
        create_team("A", "tokenA", "pmA", "teamI"),
        request(
            "internal team - no service identity",
            "GET",
            "teams",
            "/internal/teams/{{teamI}}",
            token="tokenA",
            tests=[status(403)],
        ),
        request(
            "internal team - forged service token",
            "GET",
            "teams",
            "/internal/teams/{{teamI}}",
            token="tokenA",
            service_token="forged.service.token",
            tests=[status(403)],
        ),
    ]
    return collection(
        "ASE - teams",
        "Component tests for the teams service: team composition, ownership and the game rules.",
        [folder("Setup", setup), folder("Public API", public), folder("Internal API", internal)],
    )


def encounters_collection():
    setup = [
        register("A", "userA", "tokenA", "playerA"),
        read_collection("A", "tokenA", "pmA"),
        create_team("A", "tokenA", "pmA", "teamA"),
        register("B", "userB", "tokenB"),
    ]
    public = [
        request(
            "list encounters - valid",
            "GET",
            "encounters",
            "/encounters",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "templates describe the wild monster and difficulty",
                    "pm.expect(b.length).to.be.at.least(3); b.forEach(t => { pm.expect(t.difficulty).to.be.within(1, 5); pm.expect(t.wild_monster.name).to.be.a('string'); });",
                ),
            ],
        ),
        no_token("list encounters - missing token", "GET", "encounters", "/encounters"),
        request(
            "xp before - read the starting progress",
            "GET",
            "players",
            "/players/me",
            token="tokenA",
            tests=[status(200), save("xpBefore", "pm.response.json().xp")],
        ),
        request(
            "start encounter - valid team",
            "POST",
            "encounters",
            "/encounters",
            token="tokenA",
            body='{"team_id":{{teamA}},"template_id":1}',
            tests=[
                status(201),
                body_check(
                    "resolved server-side with a recorded outcome and progress",
                    "pm.expect(['win','loss']).to.include(b.outcome); pm.expect(b.details.resolution.success).to.equal(b.outcome === 'win'); pm.expect(b.details.team).to.have.lengthOf(3); pm.expect(b.progress.xp_gained).to.be.above(0); pm.expect(b.progress.xp).to.equal(Number(pm.collectionVariables.get('xpBefore')) + b.progress.xp_gained);",
                ),
                save("encounterA", "pm.response.json().id"),
                save("outcomeA", "pm.response.json().outcome"),
                save("xpGainedA", "pm.response.json().progress.xp_gained"),
            ],
        ),
        request(
            "xp after - the server saved exactly what it announced",
            "GET",
            "players",
            "/players/me",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "xp is the starting xp plus the xp of the encounter, level is valid",
                    "const expected = Number(pm.collectionVariables.get('xpBefore')) + Number(pm.collectionVariables.get('xpGainedA')); pm.expect(b.xp).to.equal(expected); pm.expect(b.level).to.be.a('number').and.to.be.at.least(1);",
                ),
            ],
        ),
        request(
            "start encounter - team of another player",
            "POST",
            "encounters",
            "/encounters",
            token="tokenB",
            body='{"team_id":{{teamA}},"template_id":1}',
            tests=[status(403)],
        ),
        request(
            "start encounter - unknown team",
            "POST",
            "encounters",
            "/encounters",
            token="tokenA",
            body={"team_id": 999999, "template_id": 1},
            tests=[status(404)],
        ),
        request(
            "start encounter - unknown template",
            "POST",
            "encounters",
            "/encounters",
            token="tokenA",
            body='{"team_id":{{teamA}},"template_id":999999}',
            tests=[status(404)],
        ),
        request(
            "start encounter - ids are not integers",
            "POST",
            "encounters",
            "/encounters",
            token="tokenA",
            body={"team_id": "1", "template_id": "1"},
            tests=[status(400)],
        ),
        request(
            "start encounter - empty body",
            "POST",
            "encounters",
            "/encounters",
            token="tokenA",
            body={},
            tests=[status(400)],
        ),
        no_token(
            "start encounter - missing token",
            "POST",
            "encounters",
            "/encounters",
            body={"team_id": 1, "template_id": 1},
        ),
        request(
            "client cannot choose the outcome",
            "POST",
            "encounters",
            "/encounters",
            token="tokenA",
            body='{"team_id":{{teamA}},"template_id":5,"outcome":"win","xp_gained":9999}',
            tests=[
                status(201),
                body_check(
                    "extra fields are ignored",
                    "pm.expect(b.progress.xp_gained).to.be.above(0);",
                ),
            ],
        ),
        request(
            "encounter details - owner",
            "GET",
            "encounters",
            "/encounters/{{encounterA}}",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "same outcome as when it was resolved",
                    "pm.expect(b.outcome).to.equal(pm.collectionVariables.get('outcomeA'));",
                ),
            ],
        ),
        request(
            "encounter details - another player",
            "GET",
            "encounters",
            "/encounters/{{encounterA}}",
            token="tokenB",
            tests=[status(404)],
        ),
        request(
            "encounter details - unknown id",
            "GET",
            "encounters",
            "/encounters/999999",
            token="tokenA",
            tests=[status(404)],
        ),
        no_token("encounter details - missing token", "GET", "encounters", "/encounters/1"),
    ]
    return collection(
        "ASE - encounters",
        "Component tests for the encounters service: templates, server-side resolution, ownership and persistence.",
        [folder("Setup", setup), folder("Public API", public)],
    )


def history_collection():
    setup = [
        register("A", "userA", "tokenA", "playerA"),
        read_collection("A", "tokenA", "pmA"),
        create_team("A", "tokenA", "pmA", "teamA"),
        register("B", "userB", "tokenB"),
        request(
            "setup: play one encounter",
            "POST",
            "encounters",
            "/encounters",
            token="tokenA",
            body='{"team_id":{{teamA}},"template_id":1}',
            tests=[
                status(201),
                save("encounterA", "pm.response.json().id"),
                save("outcomeA", "pm.response.json().outcome"),
            ],
        ),
    ]
    public = [
        request(
            "history - valid",
            "GET",
            "history",
            "/history",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "the encounter is listed with a summary",
                    "pm.expect(b).to.have.lengthOf(1); pm.expect(b[0].id).to.equal(Number(pm.collectionVariables.get('encounterA'))); pm.expect(b[0].outcome).to.equal(pm.collectionVariables.get('outcomeA')); pm.expect(b[0].summary.team).to.have.lengthOf(3);",
                ),
            ],
        ),
        request(
            "history - limit parameter",
            "GET",
            "history",
            "/history",
            token="tokenA",
            query={"limit": 1},
            tests=[
                status(200),
                body_check("at most one entry", "pm.expect(b.length).to.be.at.most(1);"),
            ],
        ),
        request(
            "history - invalid limit",
            "GET",
            "history",
            "/history",
            token="tokenA",
            query={"limit": "abc"},
            tests=[status(400)],
        ),
        request(
            "history - limit below 1",
            "GET",
            "history",
            "/history",
            token="tokenA",
            query={"limit": 0},
            tests=[status(400)],
        ),
        request(
            "history - other player has none",
            "GET",
            "history",
            "/history",
            token="tokenB",
            tests=[status(200), body_check("empty list", "pm.expect(b).to.have.lengthOf(0);")],
        ),
        no_token("history - missing token", "GET", "history", "/history"),
        request(
            "history detail - owner",
            "GET",
            "history",
            "/history/{{encounterA}}",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "full snapshot of team and resolution",
                    "pm.expect(b.details.team).to.have.lengthOf(3); pm.expect(b.details.resolution).to.be.an('object'); pm.expect(b.details.wild_monster.name).to.be.a('string');",
                ),
            ],
        ),
        request(
            "history detail - another player",
            "GET",
            "history",
            "/history/{{encounterA}}",
            token="tokenB",
            tests=[status(404)],
        ),
        request(
            "history detail - unknown id",
            "GET",
            "history",
            "/history/999999",
            token="tokenA",
            tests=[status(404)],
        ),
        no_token("history detail - missing token", "GET", "history", "/history/1"),
        request(
            "history detail - snapshot survives deleting the team",
            "DELETE",
            "teams",
            "/teams/{{teamA}}",
            token="tokenA",
            tests=[status(204)],
        ),
        request(
            "history detail - still available after the team is gone",
            "GET",
            "history",
            "/history/{{encounterA}}",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "team snapshot is intact", "pm.expect(b.details.team).to.have.lengthOf(3);"
                ),
            ],
        ),
    ]
    return collection(
        "ASE - history",
        "Component tests for the history service: permanent, per-player record of past encounters.",
        [folder("Setup", setup), folder("Public API", public)],
    )


def acceptance_collection():
    """End-to-end scenario through the gateway that checks the backlog acceptance criteria."""
    sequence = [
        request(
            "US-01 register creates an account and returns a token",
            "POST",
            "auth",
            "/auth/register",
            body={"username": "{{userA}}", "password": PASSWORD},
            pre=new_username("userA"),
            tests=[status(201), save("tokenA", "pm.response.json().access_token")],
        ),
        request(
            "US-01 duplicate username is refused",
            "POST",
            "auth",
            "/auth/register",
            body={"username": "{{userA}}", "password": PASSWORD},
            tests=[status(409)],
        ),
        request(
            "US-06 new player owns the initial collection",
            "GET",
            "players",
            "/players/me/monsters",
            token="tokenA",
            tests=[
                status(200),
                body_check("3 monsters", "pm.expect(b).to.have.lengthOf(3);"),
                save("pmA1", "pm.response.json()[0].player_monster_id"),
                save("pmA2", "pm.response.json()[1].player_monster_id"),
                save("pmA3", "pm.response.json()[2].player_monster_id"),
            ],
        ),
        request(
            "US-10 US-11 catalogue and details are public and include an image",
            "GET",
            "monsters",
            "/monsters/1",
            tests=[
                status(200),
                body_check(
                    "image and stats",
                    "pm.expect(b.image_url).to.be.a('string'); pm.expect(b.attack).to.be.a('number');",
                ),
            ],
        ),
        request(
            "US-16 team with a monster I do not own is rejected",
            "POST",
            "teams",
            "/teams",
            token="tokenA",
            body={"name": "bad", "member_ids": [999999]},
            tests=[status(422)],
        ),
        create_team("A", "tokenA", "pmA", "teamA"),
        request(
            "US-18 US-19 encounter is resolved by the server",
            "POST",
            "encounters",
            "/encounters",
            token="tokenA",
            body='{"team_id":{{teamA}},"template_id":1}',
            tests=[
                status(201),
                body_check(
                    "success or failure recorded",
                    "pm.expect(['win','loss']).to.include(b.outcome);",
                ),
                save("encounter1", "pm.response.json().id"),
                save("xp1", "pm.response.json().progress.xp_gained"),
                save(
                    "reward1",
                    "pm.response.json().details.reward ? pm.response.json().details.reward.id : 0",
                ),
            ],
        ),
        request(
            "US-12 a reward is added to the collection (and only then)",
            "GET",
            "players",
            "/players/me/monsters",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "collection grew exactly when a reward was granted",
                    "const reward = Number(pm.collectionVariables.get('reward1')); pm.expect(b.length).to.equal(reward ? 4 : 3); if (reward) { pm.expect(b.map(m => m.monster_id)).to.include(reward); }",
                ),
            ],
        ),
        request(
            "US-25 progress was saved together with the result",
            "GET",
            "players",
            "/players/me",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "xp equals the xp of the encounter",
                    "pm.expect(b.xp).to.equal(Number(pm.collectionVariables.get('xp1')));",
                ),
            ],
        ),
        request(
            "US-02 US-03 login again and find the same progress",
            "POST",
            "auth",
            "/auth/login",
            body={"username": "{{userA}}", "password": PASSWORD},
            tests=[status(200), save("tokenA", "pm.response.json().access_token")],
        ),
        request(
            "US-03 progress is still there after login",
            "GET",
            "players",
            "/players/me",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "same xp and collection",
                    "pm.expect(b.xp).to.equal(Number(pm.collectionVariables.get('xp1'))); pm.expect(b.monsters_owned).to.be.at.least(3);",
                ),
            ],
        ),
        request(
            "US-23 US-24 history lists the encounter and shows its details",
            "GET",
            "history",
            "/history/{{encounter1}}",
            token="tokenA",
            tests=[
                status(200),
                body_check("snapshot present", "pm.expect(b.details.team).to.have.lengthOf(3);"),
            ],
        ),
        request(
            "US-20 result details are available",
            "GET",
            "encounters",
            "/encounters/{{encounter1}}",
            token="tokenA",
            tests=[
                status(200),
                body_check(
                    "team and wild monster",
                    "pm.expect(b.details.wild_monster.name).to.be.a('string');",
                ),
            ],
        ),
        register("B", "userB", "tokenB"),
        request(
            "US-05 another player cannot read my history",
            "GET",
            "history",
            "/history/{{encounter1}}",
            token="tokenB",
            tests=[status(404)],
        ),
        request(
            "US-05 another player cannot use my team",
            "POST",
            "encounters",
            "/encounters",
            token="tokenB",
            body='{"team_id":{{teamA}},"template_id":1}',
            tests=[status(403)],
        ),
        request(
            "US-05 logout revokes my token",
            "POST",
            "auth",
            "/auth/logout",
            token="tokenA",
            tests=[status(204)],
        ),
        request(
            "US-05 a revoked token is refused at once",
            "GET",
            "players",
            "/players/me",
            token="tokenA",
            tests=[
                status(401),
                body_check("reason is explained", "pm.expect(b.message).to.include('revoked');"),
            ],
        ),
        request(
            "US-05 other sessions are not affected by my logout",
            "GET",
            "players",
            "/players/me",
            token="tokenB",
            tests=[status(200)],
        ),
        request(
            "US-05 expired or forged tokens are refused",
            "GET",
            "players",
            "/players/me",
            token="!forged.token.value",
            tests=[status(401)],
        ),
    ]
    return collection(
        "ASE - acceptance (end to end)",
        "Scenario through the gateway verifying the acceptance criteria of the mandatory user stories.",
        [folder("Scenario", sequence)],
    )


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
        "auth": auth_collection,
        "players": players_collection,
        "monsters": monsters_collection,
        "teams": teams_collection,
        "encounters": encounters_collection,
        "history": history_collection,
        "acceptance": acceptance_collection,
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

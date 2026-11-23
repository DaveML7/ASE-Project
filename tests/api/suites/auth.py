"""Postman requests for the auth service."""

from .dsl import (
    PASSWORD,
    body_check,
    check,
    collection,
    folder,
    new_username,
    no_token,
    request,
    save,
    status,
)


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

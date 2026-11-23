"""Postman requests for the history service."""

from .dsl import (
    body_check,
    collection,
    create_team,
    folder,
    no_token,
    read_collection,
    register,
    request,
    save,
    status,
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

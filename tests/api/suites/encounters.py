"""Postman requests for the encounters service."""

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

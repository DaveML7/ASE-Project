"""Postman requests for the acceptance service."""

from .dsl import (
    PASSWORD,
    body_check,
    collection,
    create_team,
    folder,
    new_username,
    register,
    request,
    save,
    status,
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

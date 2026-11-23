"""Postman requests for the players service."""

from .dsl import (
    body_check,
    collection,
    folder,
    no_token,
    register,
    request,
    save,
    status,
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

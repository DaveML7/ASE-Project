"""Postman requests for the monsters service."""

from .dsl import (
    body_check,
    check,
    collection,
    folder,
    request,
    status,
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

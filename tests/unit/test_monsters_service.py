"""Monsters service: validation, the cached lookup and the artwork."""

import importlib.util
import pathlib

import pytest

SERVICE = pathlib.Path(__file__).parents[2] / "services" / "monsters" / "app.py"
CATALOGUE = [
    {
        "id": n,
        "name": name,
        "element": element,
        "hp": 10,
        "attack": 5,
        "defence": 3,
        "rarity": "common",
        "description": "",
    }
    for n, name, element in [(1, "Cinderpup", "fire"), (2, 'A<b>"&', "water"), (3, "", "air")]
]


class FakeDb:
    def __init__(self):
        self.catalogue_reads = 0

    def catalogue(self):
        self.catalogue_reads += 1
        return CATALOGUE

    def catalogue_by_id(self):
        return {m["id"]: m for m in CATALOGUE}

    def get(self, *args):  # the service must use the cached catalogue instead
        raise AssertionError("monsters must not query the database per request")


@pytest.fixture
def monsters(key_dir, monkeypatch):
    # the monsters service only needs its own key, like in production
    (key_dir.path / "jwt_public.pem").unlink()
    from ase_common import clear_cache

    clear_cache()
    spec = importlib.util.spec_from_file_location("monsters_app", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "db", FakeDb())
    return module


def test_catalogue_lists_monsters_with_image_and_characteristics(monsters):
    body = monsters.app.test_client().get("/monsters").json
    assert [m["id"] for m in body] == [1, 2, 3]
    assert body[0]["image_url"] == "/monsters/1/image" and body[0]["attack"] == 5


@pytest.mark.parametrize("limit, status", [("2", 200), ("abc", 400), ("0", 400), ("-5", 400)])
def test_limit_is_validated(monsters, limit, status):
    assert monsters.app.test_client().get(f"/monsters?limit={limit}").status_code == status


def test_limit_is_capped_and_applied(monsters):
    client = monsters.app.test_client()
    assert len(client.get("/monsters?limit=2").json) == 2
    assert len(client.get("/monsters?limit=99999").json) == 3


def test_details_and_unknown_monster(monsters):
    client = monsters.app.test_client()
    assert client.get("/monsters/1").json["name"] == "Cinderpup"
    assert client.get("/monsters/999").status_code == 404
    assert client.get("/monsters/abc").status_code == 404


def test_artwork_is_a_cacheable_svg_with_escaped_text(monsters):
    response = monsters.app.test_client().get("/monsters/2/image")
    assert response.status_code == 200 and response.mimetype == "image/svg+xml"
    assert "max-age=3600" in response.headers["Cache-Control"]
    text = response.get_data(as_text=True)
    assert "&lt;b&gt;" in text and "<b>" not in text


def test_a_monster_without_a_name_still_gets_artwork(monsters):
    assert monsters.app.test_client().get("/monsters/3/image").status_code == 200


def test_unknown_artwork_is_404(monsters):
    assert monsters.app.test_client().get("/monsters/999/image").status_code == 404


def test_the_catalogue_is_cacheable_and_answers_304_when_nothing_changed(monsters):
    client = monsters.app.test_client()
    first = client.get("/monsters")
    assert first.status_code == 200 and "max-age=60" in first.headers["Cache-Control"]
    etag = first.headers["ETag"]
    again = client.get("/monsters", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.get_data() == b""
    assert client.get("/monsters", headers={"If-None-Match": '"other"'}).status_code == 200
    assert client.get("/monsters/1").headers["ETag"]

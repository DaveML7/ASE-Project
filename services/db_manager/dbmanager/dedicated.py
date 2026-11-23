"""The two operations that are more than one generic statement."""

from flask import abort
from psycopg2 import sql

from . import repository
from .sql import table_ref

MAX_TEAM_SIZE = 3


def _set_members(cursor, team: dict, members: list[int], name: str | None) -> dict:
    """Rename `team` and replace its members, after checking that the team's owner owns
    every monster. The caller holds the row lock of the team."""
    cursor.execute(
        sql.SQL("SELECT count(*) AS n FROM {} WHERE player_id = %s AND id = ANY(%s)").format(
            table_ref("player_monsters")
        ),
        [team["player_id"], members],
    )
    if cursor.fetchone()["n"] != len(members):
        abort(409, "team members must belong to the team owner")
    if name is not None:
        team = repository.update(cursor, "teams", team["id"], {"name": name})
    cursor.execute(
        sql.SQL("DELETE FROM {} WHERE team_id = %s").format(table_ref("team_slots")),
        [team["id"]],
    )
    for slot, member in enumerate(members, start=1):
        repository.insert(
            cursor, "team_slots", {"team_id": team["id"], "slot": slot, "player_monster_id": member}
        )
    return {"id": team["id"], "name": team["name"], "member_ids": members}


def replace_team_slots(cursor, team_id: int, members: list[int], name: str | None) -> dict:
    """Rename a team and replace its members in ONE transaction (the row lock serialises
    concurrent edits)."""
    cursor.execute(
        sql.SQL("SELECT * FROM {} WHERE id = %s FOR UPDATE").format(table_ref("teams")), [team_id]
    )
    team = cursor.fetchone()
    if team is None:
        abort(404, "team not found")
    return _set_members(cursor, team, members, name)


def create_team(cursor, player_id: str, name: str, members: list[int]) -> dict:
    """Create a team and its members in ONE transaction: either all of it exists or none of
    it does (no half-created team, whatever fails or is lost). The per-player limit is
    enforced by a database trigger, so concurrent requests cannot exceed it."""
    team = repository.insert(cursor, "teams", {"player_id": player_id, "name": name})
    return _set_members(cursor, team, members, None)


def purge_expired_revocations(cursor) -> int:
    """Delete revoked-token entries whose token has expired anyway (they can never be
    presented again), so the table does not grow for ever. Returns how many were removed."""
    cursor.execute(
        sql.SQL("DELETE FROM {} WHERE expires_at < now()").format(table_ref("revoked_tokens"))
    )
    return cursor.rowcount


def encounter_history(cursor, player_id: str, before: int | None, limit: int) -> list[dict]:
    """A page of a player's encounters, newest first, after the cursor `before`.

    Only the fields the list shows are read out of the JSON snapshot (the detail endpoint
    reads the whole thing): a row is a few hundred bytes instead of a few kilobytes.
    """
    encounters = table_ref("encounters")
    cursor.execute(
        sql.SQL(
            """
            SELECT id, template_id, wild_monster_id, outcome, created_at,
                   jsonb_build_object(
                       'template', details->'template',
                       'wild_monster', jsonb_build_object('name', details->'wild_monster'->'name'),
                       'reward', jsonb_build_object('name', details->'reward'->'name'),
                       'team', CASE WHEN jsonb_typeof(details->'team') = 'array' THEN (
                           SELECT coalesce(jsonb_agg(jsonb_build_object('name', member->'name')),
                                           '[]'::jsonb)
                           FROM jsonb_array_elements(details->'team') AS member
                       ) ELSE '[]'::jsonb END
                   ) AS details
            FROM {encounters}
            WHERE player_id = %s AND (%s::bigint IS NULL OR (created_at, id) < (
                SELECT created_at, id FROM {encounters} WHERE id = %s AND player_id = %s
            ))
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """
        ).format(encounters=encounters),
        [player_id, before, before, player_id, limit],
    )
    return cursor.fetchall()

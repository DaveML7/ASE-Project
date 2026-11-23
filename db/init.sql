-- Monster Tamers schema + seed data.
-- Executed by the postgres container on first start (docker compose down -v to reset).

-- One schema per microservice: every service's data is physically separate, and the
-- db-manager runs each caller's statements with that service's own role (db/app-access.sql).
CREATE SCHEMA IF NOT EXISTS auth_data;
CREATE SCHEMA IF NOT EXISTS encounters_data;
CREATE SCHEMA IF NOT EXISTS monsters_data;
CREATE SCHEMA IF NOT EXISTS players_data;
CREATE SCHEMA IF NOT EXISTS teams_data;


CREATE TABLE players_data.players (
    id          uuid PRIMARY KEY,
    username    text NOT NULL UNIQUE CHECK (username = lower(username)),
    avatar      text NOT NULL DEFAULT 'default',
    faction     text,
    xp          integer NOT NULL DEFAULT 0 CHECK (xp >= 0),
    -- Derived, never written by services: level and xp cannot drift apart.
    -- Keep the divisor in sync with XP_PER_LEVEL in services/encounters/app.py.
    level       integer GENERATED ALWAYS AS (1 + xp / 100) STORED,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Owned by the auth service; declared without an FK to players because the
-- credential row is written before the player row (cross-service ownership).
-- password_hash holds an scrypt hash that the db-manager additionally encrypts
-- (AES-256-GCM, "enc:v1:..." values) before it reaches this table.
CREATE TABLE auth_data.credentials (
    id            bigserial PRIMARY KEY,
    player_id     uuid NOT NULL UNIQUE,
    username      text NOT NULL UNIQUE CHECK (username = lower(username)),
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE monsters_data.monsters (
    id          bigserial PRIMARY KEY,
    name        text NOT NULL,
    element     text NOT NULL CHECK (element IN ('fire', 'water', 'nature', 'air', 'earth')),
    hp          integer NOT NULL CHECK (hp > 0),
    attack      integer NOT NULL CHECK (attack > 0),
    defence     integer NOT NULL CHECK (defence >= 0),
    rarity      text NOT NULL DEFAULT 'common' CHECK (rarity IN ('common', 'rare', 'epic', 'legendary')),
    description text NOT NULL DEFAULT ''
);

CREATE TABLE players_data.player_monsters (
    id         bigserial PRIMARY KEY,
    player_id  uuid NOT NULL REFERENCES players_data.players(id) ON DELETE CASCADE,
    monster_id bigint NOT NULL REFERENCES monsters_data.monsters(id),
    nickname   text,
    level      integer NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 100),
    source     text NOT NULL DEFAULT 'starter',
    acquired_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (player_id, monster_id)
);

CREATE TABLE teams_data.teams (
    id         bigserial PRIMARY KEY,
    player_id  uuid NOT NULL REFERENCES players_data.players(id) ON DELETE CASCADE,
    name       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- A player owns at most 20 teams (the same number as TEAMS_PER_PLAYER in the teams service).
-- Enforced here, under a per-player lock, so that parallel requests cannot go past it.
CREATE FUNCTION teams_data.enforce_team_limit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.player_id::text, 0));
    IF (SELECT count(*) FROM teams_data.teams WHERE player_id = NEW.player_id) >= 20 THEN
        RAISE EXCEPTION 'team limit reached' USING ERRCODE = '23514', CONSTRAINT = 'teams_per_player_limit';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER teams_per_player_limit BEFORE INSERT ON teams_data.teams
    FOR EACH ROW EXECUTE FUNCTION teams_data.enforce_team_limit();

CREATE TABLE teams_data.team_slots (
    id                bigserial PRIMARY KEY,
    team_id           bigint NOT NULL REFERENCES teams_data.teams(id) ON DELETE CASCADE,
    slot              integer NOT NULL CHECK (slot BETWEEN 1 AND 3),
    player_monster_id bigint NOT NULL REFERENCES players_data.player_monsters(id) ON DELETE CASCADE,
    UNIQUE (team_id, slot),
    UNIQUE (team_id, player_monster_id)
);

CREATE TABLE encounters_data.encounter_templates (
    id              bigserial PRIMARY KEY,
    name            text NOT NULL,
    wild_monster_id bigint NOT NULL REFERENCES monsters_data.monsters(id),
    difficulty      integer NOT NULL DEFAULT 1 CHECK (difficulty BETWEEN 1 AND 5),
    description     text NOT NULL DEFAULT ''
);

-- No FK on team_id: a team may be edited or deleted later, history must survive.
-- The resolved team is snapshotted inside details.
CREATE TABLE encounters_data.encounters (
    id              bigserial PRIMARY KEY,
    player_id       uuid NOT NULL REFERENCES players_data.players(id) ON DELETE CASCADE,
    team_id         bigint NOT NULL,
    template_id     bigint NOT NULL,
    wild_monster_id bigint NOT NULL REFERENCES monsters_data.monsters(id),
    outcome         text NOT NULL CHECK (outcome IN ('win', 'loss')),
    details         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Logged-out (revoked) access tokens, kept until they would have expired anyway.
CREATE TABLE auth_data.revoked_tokens (
    id         bigserial PRIMARY KEY,
    jti        text NOT NULL UNIQUE,
    player_id  uuid NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE INDEX revoked_tokens_expires_idx ON auth_data.revoked_tokens (expires_at);
CREATE INDEX encounters_player_created_idx ON encounters_data.encounters (player_id, created_at DESC, id DESC);
CREATE INDEX player_monsters_player_idx ON players_data.player_monsters (player_id);

INSERT INTO monsters_data.monsters (name, element, hp, attack, defence, rarity, description) VALUES
    ('Cinderpup',   'fire',   45, 14, 6,  'common',    'A young ember hound that sparks when excited.'),
    ('Flameox',     'fire',   78, 22, 11, 'rare',      'Its horns glow hot enough to melt stone.'),
    ('Pyrelisk',    'fire',   95, 30, 14, 'epic',      'A serpent of living magma.'),
    ('Dewlet',      'water',  50, 12, 9,  'common',    'A droplet spirit that follows rivers.'),
    ('Tidefin',     'water',  82, 20, 15, 'rare',      'Rides the surf of coastal storms.'),
    ('Abyssmaw',    'water',  110, 28, 18, 'legendary', 'Guardian of the deepest trench.'),
    ('Sproutle',    'nature', 55, 13, 8,  'common',    'A walking sapling with a stubborn streak.'),
    ('Thornback',   'nature', 88, 21, 17, 'rare',      'Its bark hides a lattice of thorns.'),
    ('Verdantaur',  'nature', 120, 26, 20, 'epic',     'An ancient grove given four legs.'),
    ('Zephyrling',  'air',    40, 16, 5,  'common',    'Rides gusts and steals hats.'),
    ('Galecrow',    'air',    80, 24, 10, 'rare',      'Dives faster than its shadow.'),
    ('Terrabeetle', 'earth',  90, 17, 24, 'common',    'Armoured, patient and immovable.'),
    ('Stonehide',   'earth',  105, 19, 30, 'rare',     'Sleeps like a boulder, hits like an avalanche.');

INSERT INTO encounters_data.encounter_templates (name, wild_monster_id, difficulty, description) VALUES
    ('Forest Ambush',   7,  1, 'A Sproutle blocks the trail through the woods.'),
    ('River Crossing',  4,  2, 'Something stirs beneath the shallows.'),
    ('Ashfall Ridge',   2,  3, 'A Flameox patrols the ridge above the vents.'),
    ('Skyline Duel',    11, 4, 'A Galecrow circles, looking for a challenger.'),
    ('Deep Trench',     6,  5, 'An Abyssmaw rises from the dark water.');

-- Monster Tamers schema + seed data.
-- Executed by the postgres container on first start (docker compose down -v to reset).

CREATE TABLE players (
    id          uuid PRIMARY KEY,
    username    text NOT NULL UNIQUE,
    avatar      text NOT NULL DEFAULT 'default',
    faction     text,
    level       integer NOT NULL DEFAULT 1,
    xp          integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Owned by the auth service; declared without an FK to players because the
-- credential row is written before the player row (cross-service ownership).
CREATE TABLE credentials (
    id            bigserial PRIMARY KEY,
    player_id     uuid NOT NULL UNIQUE,
    username      text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE monsters (
    id          bigserial PRIMARY KEY,
    name        text NOT NULL,
    element     text NOT NULL CHECK (element IN ('fire', 'water', 'nature', 'air', 'earth')),
    hp          integer NOT NULL CHECK (hp > 0),
    attack      integer NOT NULL CHECK (attack > 0),
    defence     integer NOT NULL CHECK (defence >= 0),
    rarity      text NOT NULL DEFAULT 'common' CHECK (rarity IN ('common', 'rare', 'epic', 'legendary')),
    description text NOT NULL DEFAULT ''
);

CREATE TABLE player_monsters (
    id         bigserial PRIMARY KEY,
    player_id  uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    monster_id bigint NOT NULL REFERENCES monsters(id),
    nickname   text,
    level      integer NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 100),
    source     text NOT NULL DEFAULT 'starter',
    acquired_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (player_id, monster_id)
);

CREATE TABLE teams (
    id         bigserial PRIMARY KEY,
    player_id  uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    name       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE team_slots (
    id                bigserial PRIMARY KEY,
    team_id           bigint NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    slot              integer NOT NULL CHECK (slot BETWEEN 1 AND 3),
    player_monster_id bigint NOT NULL REFERENCES player_monsters(id) ON DELETE CASCADE,
    UNIQUE (team_id, slot),
    UNIQUE (team_id, player_monster_id)
);

CREATE TABLE encounter_templates (
    id              bigserial PRIMARY KEY,
    name            text NOT NULL,
    wild_monster_id bigint NOT NULL REFERENCES monsters(id),
    difficulty      integer NOT NULL DEFAULT 1 CHECK (difficulty BETWEEN 1 AND 5),
    description     text NOT NULL DEFAULT ''
);

-- No FK on team_id: a team may be edited or deleted later, history must survive.
-- The resolved team is snapshotted inside details.
CREATE TABLE encounters (
    id              bigserial PRIMARY KEY,
    player_id       uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    team_id         bigint NOT NULL,
    template_id     bigint NOT NULL,
    wild_monster_id bigint NOT NULL REFERENCES monsters(id),
    outcome         text NOT NULL CHECK (outcome IN ('win', 'loss')),
    details         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX encounters_player_created_idx ON encounters (player_id, created_at DESC);
CREATE INDEX player_monsters_player_idx ON player_monsters (player_id);

INSERT INTO monsters (name, element, hp, attack, defence, rarity, description) VALUES
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

INSERT INTO encounter_templates (name, wild_monster_id, difficulty, description) VALUES
    ('Forest Ambush',   7,  1, 'A Sproutle blocks the trail through the woods.'),
    ('River Crossing',  4,  2, 'Something stirs beneath the shallows.'),
    ('Ashfall Ridge',   2,  3, 'A Flameox patrols the ridge above the vents.'),
    ('Skyline Duel',    11, 4, 'A Galecrow circles, looking for a challenger.'),
    ('Deep Trench',     6,  5, 'An Abyssmaw rises from the dark water.');

-- Safe to run on both fresh and existing databases; keep existing accounts active.
ALTER TABLE credentials ADD COLUMN IF NOT EXISTS registration_complete boolean NOT NULL DEFAULT true;

\getenv db_app_password DB_APP_PASSWORD
SET password_encryption = 'scram-sha-256';
SELECT 'CREATE ROLE monster_tamers_app LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'monster_tamers_app') \gexec
ALTER ROLE monster_tamers_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'db_app_password';

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO monster_tamers_app;
GRANT SELECT ON players, credentials, monsters, player_monsters, teams, team_slots,
    encounter_templates, encounters, revoked_tokens TO monster_tamers_app;
GRANT INSERT ON credentials, player_monsters, teams, team_slots, encounters, revoked_tokens TO monster_tamers_app;
GRANT INSERT (id, username, avatar, faction) ON players TO monster_tamers_app;
GRANT UPDATE (avatar, faction, xp) ON players TO monster_tamers_app;
GRANT UPDATE (registration_complete) ON credentials TO monster_tamers_app;
GRANT UPDATE (name) ON teams TO monster_tamers_app;
GRANT DELETE ON teams, team_slots TO monster_tamers_app;
GRANT USAGE ON SEQUENCE credentials_id_seq, player_monsters_id_seq, teams_id_seq, team_slots_id_seq,
    encounters_id_seq, revoked_tokens_id_seq TO monster_tamers_app;

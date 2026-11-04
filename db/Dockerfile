# Postgres plus the schema/seed script, baked in so the container never needs
# to read files from the host (bind mounts fail when the repo lives in a
# home directory the postgres user cannot traverse).
FROM postgres:16.4-alpine
COPY init.sql /docker-entrypoint-initdb.d/00-init.sql

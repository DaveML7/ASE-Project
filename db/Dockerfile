# Postgres plus the schema/seed script, baked in so the container never needs
# to read files from the host (bind mounts fail when the repo lives in a
# home directory the postgres user cannot traverse).
FROM postgres:16.16-alpine3.24
COPY init.sql /docker-entrypoint-initdb.d/00-init.sql

# The compose file always runs this image as the unprivileged `postgres` user, so
# the privilege-dropping helper gosu (a Go binary with known stdlib CVEs) is never
# used. Remove it instead of shipping an unused, vulnerable binary.
RUN rm -f /usr/local/bin/gosu

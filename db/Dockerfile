# Postgres plus the schema/seed script, baked in so the container never needs
# to read files from the host (bind mounts fail when the repo lives in a
# home directory the postgres user cannot traverse).
FROM postgres:16.16-alpine3.24
COPY init.sql /docker-entrypoint-initdb.d/00-init.sql
COPY --chmod=0755 provision-app.sh /docker-entrypoint-initdb.d/10-provision-app.sh
COPY app-access.sql /usr/local/share/app-access.sql
COPY pg_hba.conf /etc/postgresql/monster-tamers-hba.conf
COPY --chmod=0755 start.sh /usr/local/bin/start-db

# The compose file always runs this image as the unprivileged `postgres` user, so
# the privilege-dropping helper gosu (a Go binary with known stdlib CVEs) is never
# used. Remove it instead of shipping an unused, vulnerable binary.
RUN rm -f /usr/local/bin/gosu

# Pick up Alpine security fixes published after the base image was built (the image scan
# fails the build on any fixable HIGH/CRITICAL, e.g. libxml2 CVE-2026-86144).
RUN apk upgrade --no-cache
ENTRYPOINT ["start-db"]
CMD ["postgres"]

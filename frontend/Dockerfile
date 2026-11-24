# Minimal static frontend, run as a standalone container OUTSIDE the backend's
# Docker network. It never talks to microservices or databases: the browser
# calls the API gateway on https://localhost:8443 (see API_BASE in app.js).
#
# nginx comes from Alpine's own package repository (not from a prebuilt nginx image), so
# every Alpine security release of nginx is picked up by `apk upgrade` at build time.
FROM alpine:3.24

RUN apk upgrade --no-cache \
    && apk add --no-cache nginx \
    && adduser -D -H -u 101 -s /sbin/nologin web

COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html app.js /usr/share/nginx/html/

# Unprivileged, listens on 8080; all writable paths are in /tmp (run with --read-only --tmpfs /tmp).
USER 101
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]

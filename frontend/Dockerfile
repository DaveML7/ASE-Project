# Minimal static frontend, run as a standalone container OUTSIDE the backend's
# Docker network. It never talks to microservices or databases: the browser
# calls the API gateway on https://localhost:8443 (see API_BASE in app.js).
FROM nginxinc/nginx-unprivileged:1.30.7-alpine

# Pick up Alpine security fixes published after the base image was built.
USER root
RUN apk upgrade --no-cache
# Back to the unprivileged nginx user (uid 101); it listens on 8080.
USER 101

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html app.js /usr/share/nginx/html/

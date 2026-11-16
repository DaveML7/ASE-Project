# Generic image for every Monster Tamers microservice.
# Build arg SERVICE selects which folder under services/ is baked in.
# No secret and no TLS key is baked in: the `init` job provisions them at first
# start and each container mounts only its own directory at /run/app-config.

FROM python:3.12-slim

ARG SERVICE

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Pick up Debian security fixes published after the base image was built.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY common /tmp/common
RUN pip install --no-cache-dir /tmp/common && rm -rf /tmp/common

COPY services/${SERVICE}/ /app/

RUN useradd --uid 10001 --create-home appuser
USER appuser

# SERVICE_NAME/PORT are set per container in docker-compose.yml.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --certfile=/run/app-config/tls.crt --keyfile=/run/app-config/tls.key --workers ${WORKERS:-2} --access-logfile - --error-logfile - app:app"]

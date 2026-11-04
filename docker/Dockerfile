# Generic image for every Monster Tamers microservice.
# Build arg SERVICE selects which folder under services/ is baked in.
# Self-signed certificates (one CA + one cert per service) are generated at
# build time so `docker compose up` needs no manual step.

FROM alpine:3.20 AS certs
RUN apk add --no-cache openssl
COPY certs/ /src/
RUN sh /src/generate-certs.sh /certs && chmod -R a+rX /certs

FROM python:3.12-slim

ARG SERVICE
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY common /tmp/common
RUN pip install --no-cache-dir /tmp/common && rm -rf /tmp/common

COPY services/${SERVICE}/ /app/

RUN useradd --uid 10001 --create-home appuser
COPY --from=certs --chown=appuser:appuser /certs /certs

USER appuser

# SERVICE_NAME/PORT are set per container in docker-compose.yml.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --certfile=/certs/${SERVICE_NAME}.crt --keyfile=/certs/${SERVICE_NAME}.key --workers ${WORKERS:-2} --access-logfile - --error-logfile - app:app"]

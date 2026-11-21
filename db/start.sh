#!/bin/sh
set -eu

if [ "$1" = postgres ]; then
    if [ "${POSTGRES_USER:-postgres}" = monster_tamers_app ]; then
        echo 'POSTGRES_USER must not be the reserved application role monster_tamers_app' >&2
        exit 1
    fi
    # PostgreSQL requires a private, postgres-owned key; the runtime mount is read-only.
    umask 077
    key=$(mktemp)
    cp /run/app-config/tls.key "$key"
    set -- "$@" -c ssl=on -c ssl_min_protocol_version=TLSv1.3 \
        -c ssl_cert_file=/run/app-config/tls.crt -c "ssl_key_file=$key" \
        -c hba_file=/etc/postgresql/monster-tamers-hba.conf
fi
exec docker-entrypoint.sh "$@"

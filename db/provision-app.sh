#!/bin/sh
set -eu
DB_APP_PASSWORD=$(tr -d '\n' </run/app-config/db_app_password)
export DB_APP_PASSWORD
exec psql -X --no-password --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set ON_ERROR_STOP=1 --single-transaction --file /usr/local/share/app-access.sql

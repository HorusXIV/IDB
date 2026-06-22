#!/bin/bash
# Creates the application database role from the environment, so no password
# is ever written into a committed SQL file. APP_DB_PASSWORD is provided by
# docker-compose.yml (sourced from .env).
#
# Postgres runs init scripts in alphabetical order, so this 000_ script runs
# before 001_schema.sql, which only references the role.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER showertrack_app WITH PASSWORD '${APP_DB_PASSWORD}';
    GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO showertrack_app;
EOSQL

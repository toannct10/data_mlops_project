#!/bin/sh
set -eu
# Docker invokes this ONLY when its own new PostgreSQL data volume is empty.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "CREATE ROLE foody_ai_readonly LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;"
pg_restore --exit-on-error --no-owner --no-acl --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" /bootstrap/foody.dump
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ai_password="$PORTABLE_AI_PASSWORD" -f /bootstrap/roles.sql

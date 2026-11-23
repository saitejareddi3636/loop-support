#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=app_password="$LOOPP_DB_PASSWORD" <<'SQL'
CREATE ROLE loopp LOGIN PASSWORD :'app_password';
ALTER DATABASE loopp OWNER TO loopp;
CREATE EXTENSION IF NOT EXISTS vector;
SQL

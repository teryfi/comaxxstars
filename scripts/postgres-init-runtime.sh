#!/bin/sh
set -eu

runtime_password="$(cat /run/secrets/runtime_database_password)"
if [ -z "$runtime_password" ]; then
    echo "runtime database password secret is empty" >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=runtime_password="$runtime_password" <<'SQL'
SELECT format('CREATE ROLE terstars_runtime LOGIN PASSWORD %L', :'runtime_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'terstars_runtime') \gexec

GRANT CONNECT ON DATABASE terstars TO terstars_runtime;
GRANT USAGE ON SCHEMA public TO terstars_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO terstars_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO terstars_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE terstars_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO terstars_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE terstars_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO terstars_runtime;
SQL

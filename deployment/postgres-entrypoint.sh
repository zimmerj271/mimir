#!/bin/sh
set -eu

# Compose file-backed secrets retain host ownership/mode. Read as bootstrap root
# before the official entrypoint drops to postgres; keep the host file 0600.
secret=/run/secrets/postgres_bootstrap_password
if [ ! -s "$secret" ]; then
    echo 'PostgreSQL bootstrap password file is required and must be nonempty' >&2
    exit 1
fi
POSTGRES_PASSWORD=$(cat "$secret")
if [ -z "$POSTGRES_PASSWORD" ]; then
    echo 'PostgreSQL bootstrap password must be nonempty' >&2
    exit 1
fi
export POSTGRES_PASSWORD
exec /usr/local/bin/docker-entrypoint.sh "$@"

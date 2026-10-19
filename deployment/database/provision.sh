#!/bin/sh
set -eu
umask 077
export PGHOST=postgres PGPORT=5432 PGUSER=postgres PGDATABASE=postgres
export PGCONNECT_TIMEOUT=10 PSQL_HISTORY=/dev/null
read_secret() {
    value=$(cat "/run/secrets/$1")
    case "$value" in
        ''|*[!A-Za-z0-9_-]*) echo 'Invalid database secret format' >&2; exit 1 ;;
    esac
    if [ "${#value}" -lt 32 ] || [ "${#value}" -gt 128 ]; then
        echo 'Database secrets must contain 32 to 128 URL-safe characters' >&2
        exit 1
    fi
}
read_secret postgres_bootstrap_password
PGPASSWORD=$value
export PGPASSWORD

# psql encrypts locally with SCRAM. Plaintext is never SQL, argv or output.
# Existing credentials are verified, never silently rotated on restart.
provision_password() {
    role=$1
    read_secret "${role}_password"
    role_password=$value
    # Commands come from a file; stdin contains only the two password prompts.
    # The helper holds one database transaction across the check and assignment.
    printf '%s\n%s\n' "$role_password" "$role_password" |
        psql -Xq -v ON_ERROR_STOP=1 -v role="$role" \
            -f /mimir/database/provision-password.psql
    # Actual password login also rejects a changed secret with retained volumes.
    PGPASSWORD=$role_password psql -XAtq -v ON_ERROR_STOP=1 -U "$role" -d mimir -c 'SELECT 1' >/dev/null
    unset role_password value
}
case "${1:-}" in
    bootstrap)
        psql -Xq -v ON_ERROR_STOP=1 -f /mimir/database/bootstrap.sql
        provision_password mimir_migrator
        ;;
    credentials)
        for role in mimir_api mimir_worker mimir_auth mimir_maintenance; do
            provision_password "$role"
        done
        ;;
    *) echo 'Only bootstrap or credentials provisioning is allowed' >&2; exit 1 ;;
esac

#!/bin/sh
set -eu
case "${1:-}" in
    migrate|validate|info) [ "$#" -eq 1 ] || exit 1 ;;
    *) echo 'Only migrate, validate or info is allowed; clean/repair/baseline are forbidden' >&2; exit 1 ;;
esac
FLYWAY_PASSWORD=$(cat /run/secrets/mimir_migrator_password)
case "$FLYWAY_PASSWORD" in
    ''|*[!A-Za-z0-9_-]*) echo 'Invalid migration secret format' >&2; exit 1 ;;
esac
if [ "${#FLYWAY_PASSWORD}" -lt 32 ] || [ "${#FLYWAY_PASSWORD}" -gt 128 ]; then
    echo 'Migration secret must contain 32 to 128 URL-safe characters' >&2
    exit 1
fi
export FLYWAY_PASSWORD
exec /flyway/flyway "$1"

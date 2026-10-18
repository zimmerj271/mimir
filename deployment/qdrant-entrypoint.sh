#!/bin/sh
set -eu
secret=/run/secrets/qdrant_api_key
if [ ! -s "$secret" ]; then
    echo 'Qdrant API key file is required and must be nonempty' >&2
    exit 1
fi
QDRANT__SERVICE__API_KEY=$(cat "$secret")
if [ -z "$QDRANT__SERVICE__API_KEY" ]; then
    echo 'Qdrant API key must be nonempty' >&2
    exit 1
fi
export QDRANT__SERVICE__API_KEY
exec /qdrant/entrypoint.sh "$@"

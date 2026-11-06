#!/bin/sh
set -eu

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Migrations run on startup inside the app.
exec uvicorn vsniper.api.main:app --host "$API_HOST" --port "$API_PORT" --proxy-headers

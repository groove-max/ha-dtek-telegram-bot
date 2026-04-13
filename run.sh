#!/usr/bin/env bash
set -e

echo "[dtek-telegram-bot] starting..."

# s6-overlay stores env vars as files, not in the process environment.
# Export SUPERVISOR_TOKEN so Python can read it via os.environ.
for varname in SUPERVISOR_TOKEN HASSIO_TOKEN; do
    f="/run/s6/container_environment/$varname"
    if [ -f "$f" ] && [ -z "${!varname}" ]; then
        export "$varname=$(cat "$f")"
    fi
done

export PYTHONUNBUFFERED=1
exec /opt/venv/bin/python -u /app/main.py

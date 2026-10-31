#!/bin/sh
set -e

# Default PUID/PGID to 1000 if not specified
PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Ensure directories exist
mkdir -p /app/data /staging /calibre

# If running as root (standard container startup)
if [ "$(id -u)" = "0" ]; then
    # Adjust appuser UID and GID to match PUID/PGID if needed
    if [ "$PGID" != "1000" ]; then
        groupmod -o -g "$PGID" appuser 2>/dev/null || true
    fi
    if [ "$PUID" != "1000" ]; then
        usermod -o -u "$PUID" appuser 2>/dev/null || true
    fi

    # Fix ownership and permissions on data and staging directories
    chown -R appuser:appuser /app/data /staging 2>/dev/null || chmod -R 777 /app/data /staging 2>/dev/null || true

    # Drop root privileges and execute command as appuser
    exec gosu appuser "$@"
else
    # Already running as non-root (e.g. if user specified 'user:' in docker-compose)
    mkdir -p /app/data /staging 2>/dev/null || true
    exec "$@"
fi

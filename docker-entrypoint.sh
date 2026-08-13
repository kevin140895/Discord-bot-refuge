#!/bin/sh
set -eu

APP_USER="refuge"
APP_GROUP="refuge"
EXPECTED_VOLUME_PATH="/app/data"

# Railway mounts persistent volumes as root. When RAILWAY_RUN_UID=0 is set,
# initialise only the expected data mount, then immediately drop privileges.
if [ "$(id -u)" -eq 0 ]; then
    volume_path="${RAILWAY_VOLUME_MOUNT_PATH:-$EXPECTED_VOLUME_PATH}"

    case "$volume_path" in
        "$EXPECTED_VOLUME_PATH"|"$EXPECTED_VOLUME_PATH"/*)
            if [ -e "$volume_path" ]; then
                chown -R "$APP_USER:$APP_GROUP" "$volume_path"
            fi
            ;;
        *)
            echo "Refus de modifier les permissions d'un volume inattendu: $volume_path" >&2
            exit 1
            ;;
    esac

    exec gosu "$APP_USER:$APP_GROUP" "$@"
fi

exec "$@"

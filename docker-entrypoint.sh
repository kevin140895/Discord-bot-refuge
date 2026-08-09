#!/bin/sh
set -u

PROVIDER_HOME="/root/bgutil-ytdlp-pot-provider/server"

cd "$PROVIDER_HOME/node_modules"
deno run \
  --allow-env \
  --allow-net \
  --allow-ffi=. \
  --allow-read=. \
  ../src/main.ts &
provider_pid=$!

cleanup() {
  kill "$provider_pid" 2>/dev/null || true
  if [ "${bot_pid:-}" != "" ]; then
    kill "$bot_pid" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

# Le provider doit rester vivant ; yt-dlp le détectera automatiquement sur
# http://127.0.0.1:4416 et lui demandera les PO Tokens nécessaires.
sleep 1
if ! kill -0 "$provider_pid" 2>/dev/null; then
  echo "bgutil PO token provider failed to start" >&2
  wait "$provider_pid"
  exit 1
fi

cd /app
python main.py &
bot_pid=$!
wait "$bot_pid"
bot_status=$?
exit "$bot_status"

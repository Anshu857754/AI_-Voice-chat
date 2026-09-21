#!/usr/bin/env bash
# Render start script: ONE web service runs the API (uvicorn, on $PORT) and the
# LiveKit agent worker (AI Dost + AI Sathi) side by side.
#
#   Render Start Command:   bash start.sh      (Root Directory: backend)
#
# - Preflight first: missing/invalid environment variables stop the deploy with a clear list
#   (names only, values are never printed).
# - The worker runs in the background under a supervisor loop. If it crashes, the reason is
#   logged and it is restarted with backoff; the API keeps serving the whole time.
# - Exactly one worker: this script starts one supervisor loop, and the worker itself refuses to
#   start while another worker already owns the bot identities in the room (exit code 3).
# - The worker has no HTTP port, so it cannot conflict with $PORT.
#
# The three commands below can be overridden (used to test this script without real keys).
set -u
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
PREFLIGHT_CMD="${PREFLIGHT_CMD:-python -m app.agent_worker --check}"
WORKER_CMD="${WORKER_CMD:-python -m app.agent_worker}"
API_CMD="${API_CMD:-uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=*}"

log() { echo "[start.sh] $*"; }

# ---- 1. preflight ----------------------------------------------------------------------------
log "checking environment..."
if ! $PREFLIGHT_CMD; then
  log "ERROR: required environment variables are missing (list above). Set them in the Render dashboard -> Environment, then redeploy." >&2
  exit 1
fi
if [ -z "${CORS_ORIGINS:-}" ]; then
  log "WARNING: CORS_ORIGINS is empty - only http://localhost:5173 may call this API. Set it to your frontend URL (e.g. your Vercel domain)." >&2
fi

# ---- 2. worker supervisor (background) -----------------------------------------------------
worker_loop() {
  local delay=5 started code
  while true; do
    started=$SECONDS
    log "starting agent worker: $WORKER_CMD"
    $WORKER_CMD
    code=$?
    case "$code" in
      1) log "ERROR: agent worker exited with code 1: configuration problem (see log above). Retrying in ${delay}s; API keeps running." >&2 ;;
      2) log "ERROR: agent worker exited with code 2: LiveKit kicked it for a DUPLICATE identity (another worker is running). Retrying in ${delay}s; API keeps running." >&2 ;;
      3) log "ERROR: agent worker exited with code 3: another worker already owns the bots in this room (normal for a few seconds during a redeploy). Retrying in ${delay}s; API keeps running." >&2 ;;
      *) log "ERROR: agent worker exited with code $code (crash). Retrying in ${delay}s; API keeps running." >&2 ;;
    esac
    sleep "$delay"
    # a worker that ran for a while was healthy: start the backoff over
    if [ $((SECONDS - started)) -gt 120 ]; then delay=5; elif [ "$delay" -lt 60 ]; then delay=$((delay * 2)); fi
  done
}

worker_loop &
WORKER_PID=$!

# ---- 3. API in the foreground ------------------------------------------------------------------
log "starting API on 0.0.0.0:${PORT}"
$API_CMD &
API_PID=$!

shutdown() {
  log "shutting down..."
  kill -TERM "$API_PID" 2>/dev/null
  pkill -TERM -P "$WORKER_PID" 2>/dev/null   # the worker process itself
  kill -TERM "$WORKER_PID" 2>/dev/null       # the supervisor loop
}
trap shutdown TERM INT

wait "$API_PID"
API_CODE=$?
log "API exited with code ${API_CODE}"
shutdown
wait 2>/dev/null
exit "$API_CODE"

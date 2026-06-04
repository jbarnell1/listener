#!/usr/bin/env bash
# Listener dashboard + MCP control.  Usage: listener [up|down|restart|status|logs]
# Default (no arg) = up.  Launches uvicorn DETACHED (setsid) so it survives the
# terminal/SSH session closing; the app's lifespan auto-starts the MCP server.
set -uo pipefail

WEB="$HOME/listener-web/bin"
APP="/mnt/c/Listener/homelab"
LOG="/tmp/listener-web.log"

_kill(){ pkill -f '[u]vicorn app:app' 2>/dev/null; pkill -f '[m]cp_server.py' 2>/dev/null; \
         pkill -f '[w]orker.py' 2>/dev/null; true; }

up(){
  _kill; sleep 0.5
  cd "$APP" || { echo "listener: can't cd $APP"; return 1; }
  # load private creds (Gmail app password, etc.) into the app's environment
  [ -f "$HOME/.listener.env" ] && { set -a; . "$HOME/.listener.env"; set +a; }
  setsid "$WEB/uvicorn" app:app --host 0.0.0.0 --port 8000 >"$LOG" 2>&1 </dev/null &
  disown 2>/dev/null || true
  printf 'listener: starting'
  for _ in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1; then echo; status; return 0; fi
    printf '.'; sleep 0.4
  done
  echo; echo "listener: did not come up — check: listener logs"; return 1
}

down(){ _kill; echo "listener: stopped"; }

status(){
  if curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    local url; url=$(tailscale serve status 2>/dev/null | grep -oE 'https://[^ ]+' | head -1)
    echo "listener: UP   → http://localhost:8000${url:+   (phone: $url)}"
  else
    echo "listener: DOWN  (run: listener up)"
  fi
  if pgrep -f '[m]cp_server.py' >/dev/null 2>&1; then echo "  mcp server: running"
  else echo "  mcp server: stopped"; fi
  if pgrep -f '[w]orker.py' >/dev/null 2>&1; then echo "  pipeline worker: running"
  else echo "  pipeline worker: stopped"; fi
}

case "${1:-up}" in
  up|start)     up ;;
  down|stop)    down ;;
  restart|re)   down; sleep 0.5; up ;;
  status|st)    status ;;
  logs|log)     tail -n 40 -f "$LOG" ;;
  *) echo "usage: listener [up|down|restart|status|logs]" ;;
esac

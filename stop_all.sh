#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

for name in ingest watcher worker; do
  pidfile="logs/${name}.pid"
  if [[ -f "$pidfile" ]]; then
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      echo "Stopping $name (pid $pid)..."
      kill "$pid"
    else
      echo "$name (pid $pid) not running."
    fi
    rm -f "$pidfile"
  fi
done
echo "Done."

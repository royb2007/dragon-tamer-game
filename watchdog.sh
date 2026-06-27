#!/bin/bash
# watchdog.sh — keeps the Dragon Tamer server alive.
# If server.py crashes or exits for any reason, it restarts automatically.
# Usage: bash watchdog.sh   (run this INSTEAD of "python server.py" directly)

PORT=5000
RESTART_DELAY=2     # seconds to wait before restarting after a crash
MAX_RESTARTS=50      # safety cap — stop after this many restarts in a row
restart_count=0

echo "🐉 Dragon Tamer watchdog starting..."

while [ $restart_count -lt $MAX_RESTARTS ]; do
  # Free the port in case a previous process is still holding it
  fuser -k ${PORT}/tcp 2>/dev/null
  sleep 1

  echo "▶️  Starting server (attempt $((restart_count + 1)))..."
  PORT=$PORT python server.py
  exit_code=$?

  if [ $exit_code -eq 0 ]; then
    echo "✅ Server exited cleanly (code 0) — not restarting."
    break
  fi

  restart_count=$((restart_count + 1))
  echo "💥 Server crashed (exit code $exit_code). Restarting in ${RESTART_DELAY}s... (restart #$restart_count)"
  sleep $RESTART_DELAY
done

if [ $restart_count -ge $MAX_RESTARTS ]; then
  echo "⚠️  Reached max restart limit ($MAX_RESTARTS). Stopping watchdog."
  echo "    Something is fundamentally broken — check server.py for errors."
fi


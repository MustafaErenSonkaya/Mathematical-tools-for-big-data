#!/usr/bin/env bash
# macOS / Linux: start everything (app + Prometheus + Grafana).
# Run it with:  bash start.sh
set -e
cd "$(dirname "$0")"

if ! docker info >/dev/null 2>&1; then
  echo
  echo "  Docker is not running."
  echo "  Open Docker Desktop, wait until it says \"Engine running\", then run this again."
  echo
  exit 1
fi

echo
echo "  Building and starting... The first time this downloads about 1 GB and takes a few minutes."
echo
docker compose up -d --build

echo
echo "  Started! Waiting a few seconds for Grafana, then opening http://localhost:3000"
sleep 15
if command -v open >/dev/null; then open http://localhost:3000        # macOS
elif command -v xdg-open >/dev/null; then xdg-open http://localhost:3000  # Linux
fi
echo
echo "  The charts fill up during the first minute."
echo "  To stop everything, run:  bash stop.sh"
echo

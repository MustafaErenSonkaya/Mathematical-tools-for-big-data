#!/usr/bin/env bash
# macOS / Linux: stop everything. Your data is kept.
# Run it with:  bash stop.sh
cd "$(dirname "$0")"
docker compose down
echo
echo "  Stopped. Run 'bash start.sh' to start again."
echo

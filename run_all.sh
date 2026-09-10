#!/usr/bin/env bash
# Launches ingestion, chunk registration, and GPU processing as three
# independent background processes, all logging to ./logs/.
#
# Run this from inside the nix dev shell:
#   nix develop
#   ./run_all.sh
#
# Stop everything:
#   ./stop_all.sh

set -euo pipefail
cd "$(dirname "$0")"

mkdir -p logs storage/chunks storage/processed db

echo "Starting ingestion supervisor (RTSP -> chunks)..."
nohup python scripts/ingest_supervisor.py > logs/ingest_stdout.log 2>&1 &
echo $! > logs/ingest.pid

echo "Starting chunk watcher (registers finished chunks into DB)..."
nohup python scripts/chunk_watcher.py > logs/watcher_stdout.log 2>&1 &
echo $! > logs/watcher.pid

echo "Starting GPU processing worker (YOLO detection + tracking)..."
nohup python scripts/process_worker.py > logs/worker_stdout.log 2>&1 &
echo $! > logs/worker.pid

echo ""
echo "All processes started. PIDs written to logs/*.pid"
echo "Tail logs with: tail -f logs/*.log"
echo "Check status with: python scripts/report.py summary"

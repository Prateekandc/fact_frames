"""
Bridges ingestion and processing: scans storage/chunks/ for .mp4 files ffmpeg
has finished writing, and registers them in the DB as 'pending' so the GPU
worker can pick them up.

Why a separate watcher instead of having ffmpeg call something directly?
Keeps ingestion (deliverable 1/2) completely decoupled from everything else,
per your requirement -- ffmpeg never knows the DB or GPU pipeline exist.

A file is considered "finalized" once its mtime hasn't changed for
`settle_seconds` (default: 1.5x segment length) -- ffmpeg is still actively
writing the newest segment for each camera, so we skip anything too fresh.
"""

import os
import time
import logging
import yaml
from datetime import datetime, timezone

import db

CONFIG_PATH = os.environ.get(
    "PIPELINE_CONFIG", os.path.join(os.path.dirname(__file__), "..", "config", "cameras.yaml")
)
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "chunk_watcher.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("watcher")


def parse_start_time_from_filename(filename, camera_id):
    # filenames look like 20250910_143000.mp4 (from ffmpeg -strftime)
    name = os.path.basename(filename).replace(".mp4", "")
    try:
        dt = datetime.strptime(name, "%Y%m%d_%H%M%S")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        # fallback: use file mtime
        mtime = os.path.getmtime(filename)
        return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def scan_once(global_cfg, settle_seconds):
    root = global_cfg["storage_root"]
    if not os.path.isdir(root):
        return 0

    registered = 0
    now = time.time()

    for camera_id in os.listdir(root):
        cam_dir = os.path.join(root, camera_id)
        if not os.path.isdir(cam_dir):
            continue
        for date_dir in os.listdir(cam_dir):
            full_date_dir = os.path.join(cam_dir, date_dir)
            if not os.path.isdir(full_date_dir):
                continue
            for fname in os.listdir(full_date_dir):
                if not fname.endswith(".mp4"):
                    continue
                fpath = os.path.join(full_date_dir, fname)
                mtime = os.path.getmtime(fpath)
                if now - mtime < settle_seconds:
                    continue  # likely still being written by ffmpeg

                start_time = parse_start_time_from_filename(fpath, camera_id)
                chunk_id = db.register_chunk(
                    camera_id=camera_id,
                    file_path=fpath,
                    start_time_utc=start_time,
                    duration_sec=global_cfg["segment_seconds"],
                )
                if chunk_id:
                    registered += 1
    return registered


def main():
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    global_cfg = cfg["global"]

    db.init_db()
    for cam in cfg["cameras"]:
        db.upsert_camera(cam["id"], cam.get("name", cam["id"]), cam["rtsp_url"], int(cam.get("enabled", True)))

    settle_seconds = global_cfg["segment_seconds"] * 1.5
    poll_interval = 5

    log.info(f"Watching {global_cfg['storage_root']} (settle={settle_seconds}s, poll={poll_interval}s)")
    while True:
        try:
            n = scan_once(global_cfg, settle_seconds)
            if n:
                log.info(f"Registered {n} new chunk(s)")
        except Exception as e:
            log.error(f"scan error: {e}")
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()

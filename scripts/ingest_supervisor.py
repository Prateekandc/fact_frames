"""
Deliverables 1 & 2: Video Ingestion + Chunking.

For each enabled camera, this launches an ffmpeg process that:
  - connects to the RTSP stream over TCP (more reliable than UDP on LAN/factory networks)
  - copies the stream (no re-encode -> minimal CPU, near-original quality/timestamps)
  - segments it into fixed-length chunks, named with wall-clock time (strftime)
  - writes chunks into storage/chunks/<camera_id>/<YYYYMMDD>/

This process is intentionally dumb: it never touches the GPU. If ffmpeg dies
(camera reboot, network blip) the supervisor restarts it after a short delay,
so recording keeps going independent of anything happening downstream.

Run this as a long-lived process (systemd user service / tmux / nix run).
"""

import subprocess
import threading
import time
import os
import sys
import logging
import yaml

CONFIG_PATH = os.environ.get(
    "PIPELINE_CONFIG", os.path.join(os.path.dirname(__file__), "..", "config", "cameras.yaml")
)
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "ingest_supervisor.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ingest")


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def build_ffmpeg_cmd(camera, global_cfg):
    cam_dir_template = os.path.join(
        global_cfg["storage_root"], camera["id"], "%Y%m%d"
    )
    out_pattern = os.path.join(cam_dir_template, "%Y%m%d_%H%M%S.mp4")

    # Ensure today's directory exists up front; ffmpeg's -strftime won't create dirs for us
    # per-segment, so we pre-create the date directory in run_camera() before spawning.
    return [
        "ffmpeg",
        "-nostdin",
        "-loglevel", "warning",
        "-rtsp_transport", global_cfg.get("rtsp_transport", "tcp"),
        "-use_wallclock_as_timestamps", "1",
        "-i", camera["rtsp_url"],
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(global_cfg["segment_seconds"]),
        "-reset_timestamps", "1",
        "-strftime", "1",
        out_pattern,
    ]


def run_camera(camera, global_cfg, stop_event):
    cam_id = camera["id"]
    base_dir = os.path.join(global_cfg["storage_root"], cam_id)
    os.makedirs(base_dir, exist_ok=True)

    delay = global_cfg.get("reconnect_delay_sec", 5)

    while not stop_event.is_set():
        # pre-create today's date directory (ffmpeg -strftime does not mkdir)
        today_dir = os.path.join(base_dir, time.strftime("%Y%m%d"))
        os.makedirs(today_dir, exist_ok=True)

        cmd = build_ffmpeg_cmd(camera, global_cfg)
        log.info(f"[{cam_id}] starting ffmpeg: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            # stream stderr to log file for debugging without blocking
            for line in proc.stderr:
                log.warning(f"[{cam_id}] {line.decode(errors='ignore').strip()}")
            proc.wait()
            log.warning(f"[{cam_id}] ffmpeg exited with code {proc.returncode}")
        except FileNotFoundError:
            log.error("ffmpeg not found on PATH. Are you inside the nix dev shell?")
            return
        except Exception as e:
            log.error(f"[{cam_id}] unexpected error: {e}")

        if stop_event.is_set():
            break
        log.info(f"[{cam_id}] reconnecting in {delay}s...")
        time.sleep(delay)


def main():
    cfg = load_config()
    global_cfg = cfg["global"]
    cameras = [c for c in cfg["cameras"] if c.get("enabled", True)]

    if not cameras:
        log.error("No enabled cameras in config. Edit config/cameras.yaml")
        return

    log.info(f"Starting ingestion for {len(cameras)} cameras: {[c['id'] for c in cameras]}")

    stop_event = threading.Event()
    threads = []
    for cam in cameras:
        t = threading.Thread(target=run_camera, args=(cam, global_cfg, stop_event), daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down ingestion supervisor...")
        stop_event.set()
        for t in threads:
            t.join(timeout=5)


if __name__ == "__main__":
    main()

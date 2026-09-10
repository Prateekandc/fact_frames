"""
Deliverable 3: GPU Video Processing and Person Detection.

Pulls one 'pending' chunk at a time from the DB (never touches live RTSP),
runs YOLO person detection + ByteTrack tracking at a controlled sampling
FPS, and writes structured detections back to the DB.

Run multiple copies of this process if you want to use more GPU throughput
in parallel -- claim_next_pending_chunk() in db.py uses an atomic UPDATE so
workers won't double-process the same chunk.

Usage:
    python process_worker.py                # run forever, polling for work
    python process_worker.py --once          # process a single chunk and exit (good for testing)
"""

import argparse
import os
import time
import logging
from datetime import datetime, timedelta, timezone

import cv2
import yaml
from ultralytics import YOLO

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
        logging.FileHandler(os.path.join(LOG_DIR, "process_worker.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("worker")

PERSON_CLASS_ID = 0  # COCO class 0 = person

_model = None


def get_model(weights="yolo11n.pt"):
    """Lazy-load so --once/testing doesn't pay model load cost until needed."""
    global _model
    if _model is None:
        log.info(f"Loading YOLO model: {weights}")
        _model = YOLO(weights)
    return _model


def process_chunk(chunk, process_fps, model_weights="yolo11n.pt"):
    """
    Runs detection+tracking on one chunk file, sampled at process_fps.
    Returns list of detection dicts ready for db.insert_detections().
    """
    model = get_model(model_weights)
    file_path = chunk["file_path"]
    chunk_id = chunk["chunk_id"]
    camera_id = chunk["camera_id"]
    chunk_start = datetime.fromisoformat(chunk["start_time_utc"])

    cap = cv2.VideoCapture(file_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {file_path}")

    native_fps = cap.get(cv2.CAP_PROP_FPS) or 25
    frame_interval = max(1, round(native_fps / process_fps))

    detections = []
    frame_idx = 0

    # model.track() maintains tracker state across calls when persist=True,
    # giving temporary person IDs that stay stable across frames WITHIN this chunk.
    # (Cross-chunk re-identification is a deliberate future step, not attempted here.)
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            offset_sec = frame_idx / native_fps
            results = model.track(
                frame,
                classes=[PERSON_CLASS_ID],
                persist=True,
                verbose=False,
                tracker="bytetrack.yaml",
            )

            r = results[0]
            if r.boxes is not None and len(r.boxes) > 0:
                for box in r.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    conf = float(box.conf[0])
                    track_id = int(box.id[0]) if box.id is not None else None
                    ts = (chunk_start + timedelta(seconds=offset_sec)).isoformat()

                    detections.append({
                        "camera_id": camera_id,
                        "chunk_id": chunk_id,
                        "timestamp_utc": ts,
                        "frame_offset_sec": round(offset_sec, 3),
                        "person_track_id": f"{camera_id}_{chunk_id}_{track_id}" if track_id is not None else None,
                        "machine_id": None,
                        "bbox_x1": x1, "bbox_y1": y1, "bbox_x2": x2, "bbox_y2": y2,
                        "confidence": conf,
                        "class_name": "person",
                    })

        frame_idx += 1

    cap.release()
    return detections


def run_forever(process_fps, model_weights, poll_interval=3):
    log.info("Worker started, polling for pending chunks...")
    while True:
        chunk = db.claim_next_pending_chunk()
        if chunk is None:
            time.sleep(poll_interval)
            continue

        log.info(f"Processing chunk {chunk['chunk_id']} ({chunk['file_path']})")
        t0 = time.time()
        try:
            dets = process_chunk(chunk, process_fps, model_weights)
            db.insert_detections(dets)
            db.mark_chunk_done(chunk["chunk_id"])
            log.info(
                f"Chunk {chunk['chunk_id']}: {len(dets)} detections in {time.time()-t0:.1f}s"
            )
        except Exception as e:
            log.error(f"Chunk {chunk['chunk_id']} failed: {e}")
            db.mark_chunk_failed(chunk["chunk_id"], e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process a single chunk and exit")
    parser.add_argument("--weights", default="yolo11n.pt", help="YOLO weights (nano = fastest, good for prototyping)")
    args = parser.parse_args()

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    process_fps = cfg["global"]["process_fps"]

    db.init_db()

    if args.once:
        chunk = db.claim_next_pending_chunk()
        if chunk is None:
            log.info("No pending chunks.")
            return
        dets = process_chunk(chunk, process_fps, args.weights)
        db.insert_detections(dets)
        db.mark_chunk_done(chunk["chunk_id"])
        log.info(f"Done. {len(dets)} detections written for chunk {chunk['chunk_id']}.")
    else:
        run_forever(process_fps, args.weights)


if __name__ == "__main__":
    main()

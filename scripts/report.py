"""
Deliverable 5: Reporting / Evidence.

Produces basic numerical reports from the detections DB, and can look up the
exact video chunk + timestamp to visually verify any specific detection
("evidence" retrieval).

Usage:
    python report.py summary                      # overall counts per camera
    python report.py summary --hours 24            # last 24h only
    python report.py evidence --detection-id 1234   # get file path + offset to scrub to
    python report.py timeline --camera cam01 --hours 6
"""

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

import db


def summary(hours=None):
    with db.get_conn() as conn:
        where = ""
        params = ()
        if hours:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            where = "WHERE d.timestamp_utc >= ?"
            params = (cutoff,)

        rows = conn.execute(f"""
            SELECT d.camera_id,
                   COUNT(*) AS total_detections,
                   COUNT(DISTINCT d.person_track_id) AS unique_person_tracks,
                   MIN(d.timestamp_utc) AS earliest,
                   MAX(d.timestamp_utc) AS latest
            FROM detections d
            {where}
            GROUP BY d.camera_id
            ORDER BY total_detections DESC
        """, params).fetchall()

        chunk_stats = conn.execute("""
            SELECT status, COUNT(*) AS n FROM chunks GROUP BY status
        """).fetchall()

    print("=== Detection Summary" + (f" (last {hours}h)" if hours else " (all time)") + " ===")
    print(f"{'camera_id':<12}{'detections':<12}{'unique_tracks':<16}{'earliest':<22}{'latest':<22}")
    for r in rows:
        print(f"{r['camera_id']:<12}{r['total_detections']:<12}{r['unique_person_tracks']:<16}"
              f"{(r['earliest'] or '-'):<22}{(r['latest'] or '-'):<22}")

    print("\n=== Chunk Processing Status ===")
    for r in chunk_stats:
        print(f"  {r['status']:<12} {r['n']}")


def evidence(detection_id):
    with db.get_conn() as conn:
        row = conn.execute("""
            SELECT d.*, c.file_path, c.start_time_utc
            FROM detections d
            JOIN chunks c ON d.chunk_id = c.chunk_id
            WHERE d.detection_id = ?
        """, (detection_id,)).fetchone()

    if row is None:
        print(f"No detection with id {detection_id}")
        return

    print(f"Detection {detection_id}")
    print(f"  Camera:        {row['camera_id']}")
    print(f"  Person track:  {row['person_track_id']}")
    print(f"  Timestamp:     {row['timestamp_utc']}")
    print(f"  Confidence:    {row['confidence']:.2f}")
    print(f"  Bounding box:  ({row['bbox_x1']:.0f}, {row['bbox_y1']:.0f}) -> ({row['bbox_x2']:.0f}, {row['bbox_y2']:.0f})")
    print(f"  Video file:    {row['file_path']}")
    print(f"  Seek to:       {row['frame_offset_sec']:.1f}s into the chunk")
    print(f"\n  To view: ffplay -ss {row['frame_offset_sec']:.1f} \"{row['file_path']}\"")


def timeline(camera_id, hours=6):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT detection_id, timestamp_utc, person_track_id, confidence, chunk_id
            FROM detections
            WHERE camera_id = ? AND timestamp_utc >= ?
            ORDER BY timestamp_utc ASC
        """, (camera_id, cutoff)).fetchall()

    print(f"=== Timeline for {camera_id} (last {hours}h): {len(rows)} detections ===")
    for r in rows:
        print(f"  [{r['timestamp_utc']}] track={r['person_track_id']} conf={r['confidence']:.2f} "
              f"(detection_id={r['detection_id']}, chunk={r['chunk_id']})")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_summary = sub.add_parser("summary")
    p_summary.add_argument("--hours", type=int, default=None)

    p_evidence = sub.add_parser("evidence")
    p_evidence.add_argument("--detection-id", type=int, required=True)

    p_timeline = sub.add_parser("timeline")
    p_timeline.add_argument("--camera", required=True)
    p_timeline.add_argument("--hours", type=int, default=6)

    args = parser.parse_args()

    if args.cmd == "summary":
        summary(args.hours)
    elif args.cmd == "evidence":
        evidence(args.detection_id)
    elif args.cmd == "timeline":
        timeline(args.camera, args.hours)


if __name__ == "__main__":
    main()

# Factory Video Analytics Pipeline

RTSP → Recording → Chunks → GPU Processing → Person Detection → Tracking → SQLite → Reports

## 1. Deploy on your Nix server

```bash
# copy this whole folder into your working directory
cp -r video_pipeline /home/prateek/
cd /home/prateek/video_pipeline

# enter the reproducible environment
nix develop
```

If `torch` pulled from nixpkgs doesn't detect your GPU (`python -c "import torch; print(torch.cuda.is_available())"` → False),
your company's Nix setup likely pins a specific CUDA-enabled torch build already —
check for an existing `shell.nix`/`flake.nix` elsewhere in your environment providing GPU-enabled
python packages, and either import it as an input here or `pip install torch --break-system-packages`
matching the CUDA version `nvidia-smi` reports. This is the one part that's genuinely
site-specific and I can't fully pin without seeing your server's existing GPU nix config —
happy to adjust `flake.nix` if you paste it.

## 2. Configure your cameras

Edit `config/cameras.yaml` — add your 10–15 RTSP URLs, keep `segment_seconds: 20` for now.

## 3. Run everything

```bash
./run_all.sh
```

This starts three independent, decoupled processes:

| Process | File | Job |
|---|---|---|
| Ingestion | `scripts/ingest_supervisor.py` | ffmpeg per camera, records + segments, auto-restarts on failure |
| Chunk watcher | `scripts/chunk_watcher.py` | detects finished chunks, registers them in SQLite as `pending` |
| GPU worker | `scripts/process_worker.py` | pulls `pending` chunks, runs YOLO + ByteTrack, writes `detections` |

Check on it:
```bash
tail -f logs/*.log
python scripts/report.py summary
```

Stop everything:
```bash
./stop_all.sh
```

## 4. See results

```bash
python scripts/report.py summary                       # counts per camera, chunk status breakdown
python scripts/report.py timeline --camera cam01 --hours 6
python scripts/report.py evidence --detection-id 42     # exact file + timestamp to verify visually
```

## Why this shape

- **Ingestion never touches the GPU.** ffmpeg does a stream copy (`-c copy`), so recording
  speed is independent of how fast the GPU worker processes chunks — this directly fixes the
  jitter/lag problem you had processing live frames.
- **The DB is the queue.** `chunks.status` (`pending → processing → done/failed`) means you can
  run more GPU worker processes in parallel later just by launching more copies of
  `process_worker.py` — no other code changes.
- **Tracking is chunk-scoped for now.** `person_track_id` is stable *within* a chunk (ByteTrack).
  Making it stable *across* chunks/cameras (re-identification) is a deliberate next step, not
  something silently faked here.
- **Schema is graph-shaped.** `cameras`, `chunks`, `detections` map directly onto
  Camera / Video-segment / Person-Machine-Timestamp nodes, so introducing a real knowledge graph
  later is a projection of this data, not a rewrite.

## Next steps (deliberately not done yet)

1. **Machine detection** — once you have a custom-trained model, add it as a second pass in
   `process_worker.py` (or a second worker) writing into the same `detections` table with
   `class_name='machine'` and populate `machine_id`.
2. **Cross-chunk person re-identification** — if you need to track the same person across
   chunk boundaries or cameras, that's a re-ID embedding step layered on top of current tracking.
3. **Scale to 25–50 cameras** — nothing here assumes 10–15; just add rows to `cameras.yaml` and,
   if GPU throughput becomes the bottleneck, run 2–3 `process_worker.py` instances in parallel
   (the atomic `claim_next_pending_chunk()` makes this safe).
4. **Retention** — no chunk deletion is implemented yet; decide a retention window (e.g. delete
   chunks older than N days once `status='done'`) before storage fills up at 25+ cameras.

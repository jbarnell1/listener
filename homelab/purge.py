#!/usr/bin/env python3
"""Audio retention purge (ADR-021): delete captured audio older than N days while
keeping transcripts / segments / embeddings / profiles forever. Snippet playback
degrades to text-only once a chunk's audio is gone (the dashboard already 404s
gracefully on a missing file).

Scoped to data/chunks/ (the ingested .bin + derived .16k.wav) — never the DB text,
never the sample WAVs under homelab/samples/.

    python purge.py [--days 30] [--dry-run]
"""
import os
import sys
import time

import db

HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK_DIR = os.path.join(HERE, "data", "chunks")
RETAIN_DAYS = int(os.environ.get("LISTENER_AUDIO_RETAIN_DAYS", "30"))


def purge_old_audio(days=None, chunk_dir=CHUNK_DIR, dry_run=False):
    """Remove audio files older than `days` (defaults to the dashboard-tunable
    retention setting — ADR-035). Returns {removed, bytes_freed}."""
    if days is None:
        days = db.cfg(db.connect(), "audio_retain_days", RETAIN_DAYS)
    cutoff = time.time() - days * 86400
    removed, freed = 0, 0
    if os.path.isdir(chunk_dir):
        for name in os.listdir(chunk_dir):
            p = os.path.join(chunk_dir, name)
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    freed += os.path.getsize(p)
                    if not dry_run:
                        os.remove(p)
                    removed += 1
            except OSError:
                pass
    print(f"purge: {'would remove' if dry_run else 'removed'} {removed} audio file(s), "
          f"{freed / 1e6:.1f} MB (> {days}d old)", flush=True)
    return {"removed": removed, "bytes_freed": freed}


def main():
    days = None                       # None → use the dashboard-tunable retention
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    purge_old_audio(days=days, dry_run="--dry-run" in sys.argv)


if __name__ == "__main__":
    main()

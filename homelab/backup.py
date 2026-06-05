#!/usr/bin/env python3
"""Rotating local backup of listener.db — the entire memory.  ADR-030.

Uses SQLite's online backup API, so the snapshot is consistent even while the worker
is writing (WAL). Keeps the last KEEP copies under backups/ (gitignored). Optionally
mirrors offsite to Backblaze B2 (or any rclone remote) when LISTENER_B2_REMOTE is set.

    python backup.py            # snapshot now + prune old ones
    LISTENER_B2_REMOTE=b2:my-bucket/listener  python backup.py   # + offsite copy
"""
import glob
import os
import sqlite3
import subprocess
import time

import db

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(HERE, "backups")
KEEP = int(os.environ.get("LISTENER_BACKUP_KEEP", "14"))


def make_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(BACKUP_DIR, f"listener-{stamp}.db")
    src = db.connect()
    dst = sqlite3.connect(out)
    try:
        with dst:
            src.backup(dst)          # consistent snapshot (handles WAL)
    finally:
        dst.close()
        src.close()

    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "listener-*.db")))
    for f in files[:-KEEP]:          # prune oldest beyond KEEP
        try:
            os.remove(f)
        except OSError:
            pass

    remote = os.environ.get("LISTENER_B2_REMOTE")      # e.g. b2:bucket/listener
    if remote:
        try:
            subprocess.run(["rclone", "copy", out, remote], timeout=600, check=False)
            print(f"backup: mirrored to {remote}")
        except Exception as e:  # noqa: BLE001
            print(f"backup: offsite copy failed: {e}")

    print(f"backup: {out} ({os.path.getsize(out) // 1024} KB)")
    return out


if __name__ == "__main__":
    make_backup()

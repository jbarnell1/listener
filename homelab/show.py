#!/usr/bin/env python3
"""Show the latest stored transcript with speaker names (read-only DB query)."""
import sys

import db


def main() -> None:
    conn = db.connect()
    row = conn.execute(
        "SELECT id, audio_path FROM transcripts ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        print("no transcripts yet")
        return
    tid = row["id"]
    print(f"transcript #{tid}: {row['audio_path']}")
    q = ("SELECT seg.t_start, COALESCE(sp.name, 'Unknown_' || sp.id, '?') AS who, seg.text "
         "FROM segments seg LEFT JOIN speakers sp ON sp.id = seg.speaker_id "
         "WHERE seg.transcript_id = ? ORDER BY seg.t_start")
    for s in conn.execute(q, (tid,)):
        print(f"  [{s['t_start']:6.2f}]  {s['who']:<12}  {s['text']}")


if __name__ == "__main__":
    main()

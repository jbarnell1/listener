#!/usr/bin/env python3
"""Enroll a named speaker's voiceprint into SQLite.  Usage: enroll.py <name> <audio>"""
import sys

from speakerid import SpeakerDB, embed_file


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: enroll.py <name> <audio>")
    name, audio = sys.argv[1], sys.argv[2]
    sdb = SpeakerDB()
    sid = sdb.enroll(name, embed_file(audio))
    library = [f"{r['id']}:{r['name'] or 'Unknown'}/{r['status']}" for r in sdb.list_speakers()]
    print(f"Enrolled '{name}' (speaker id {sid}) from {audio}.")
    print(f"Library: {library}")


if __name__ == "__main__":
    main()

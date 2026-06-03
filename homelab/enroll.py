#!/usr/bin/env python3
"""Enroll a named speaker's voiceprint.  Usage: enroll.py <name> <audio>"""
import sys

from speakerid import SpeakerDB, embed_file


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: enroll.py <name> <audio>")
    name, audio = sys.argv[1], sys.argv[2]
    db = SpeakerDB()
    db.enroll(name, embed_file(audio))
    print(f"Enrolled '{name}' from {audio}. Library: {sorted(db.people)}")


if __name__ == "__main__":
    main()

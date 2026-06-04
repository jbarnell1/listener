#!/usr/bin/env python3
"""Dev helper: sign + POST an audio file to /ingest exactly like the device will.

Mirrors the HMAC scheme in app.py (X-Sig = HMAC-SHA256(secret, f"{ts}" + body)).
Lets us drive the whole pipeline through the real ingest path before the ESP32
firmware exists.

    python ingest_send.py samples/two.wav [seq]
"""
import hashlib
import hmac
import os
import sys
import time
import urllib.request

URL = os.environ.get("LISTENER_INGEST_URL", "http://127.0.0.1:8000/ingest")
SECRET = os.environ.get("LISTENER_INGEST_SECRET", "dev-secret-change-me").encode()


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: ingest_send.py <audio-file> [seq]")
    path = sys.argv[1]
    seq = sys.argv[2] if len(sys.argv) > 2 else "1"
    body = open(path, "rb").read()
    ts = str(int(time.time()))
    sig = hmac.new(SECRET, ts.encode() + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(URL, body, {
        "X-Sig": sig, "X-Ts": ts, "X-Seq": seq, "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"ingest {path} ({len(body)} bytes) -> {r.status} {r.read().decode()}")


if __name__ == "__main__":
    main()

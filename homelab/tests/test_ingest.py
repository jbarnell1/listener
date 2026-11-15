"""The two device endpoints: HMAC signature, replay window, per-device keys."""
import hashlib
import hmac
import json
import os
import time


def _sign(key, body, ts=None):
    ts = str(int(ts if ts is not None else time.time()))
    sig = hmac.new(key.encode(), ts.encode() + body, hashlib.sha256).hexdigest()
    return {"X-Ts": ts, "X-Sig": sig}


def test_ingest_accepts_a_signed_chunk(client, seeded):
    import db
    body = b"RIFF" + b"\x00" * 64
    r = client.post("/ingest", content=body,
                    headers={**_sign("test-shared-secret", body), "X-Seq": "7"})
    assert r.status_code == 200 and "acked" in r.json()
    row = seeded.execute("SELECT * FROM chunks WHERE id=?", (r.json()["acked"],)).fetchone()
    assert row["bytes"] == len(body) and row["transcribed"] == 0
    assert os.path.exists(row["path"])
    assert db.queue_stats(seeded)["pending"] >= 1


def test_ingest_rejects_a_bad_signature(client):
    body = b"audio"
    headers = _sign("the-wrong-secret", body)
    assert client.post("/ingest", content=body, headers=headers).status_code == 401


def test_ingest_rejects_a_replayed_timestamp(client):
    body = b"audio"
    headers = _sign("test-shared-secret", body, ts=time.time() - 3600)
    assert client.post("/ingest", content=body, headers=headers).status_code == 401


def test_ingest_requires_a_signature(client):
    assert client.post("/ingest", content=b"audio").status_code == 401


def test_per_device_key_and_revocation(client, seeded):
    import db
    key = db.create_device_key(seeded, "bench-01", "test board")
    body = json.dumps({"device": "bench-01", "battery_mv": 3900, "rssi": -60}).encode()

    ok = client.post("/telemetry", content=body,
                     headers={**_sign(key, body), "X-Device": "bench-01"})
    assert ok.status_code == 200
    assert any(d["device_id"] == "bench-01" for d in db.device_status_list(seeded))

    # the shared secret does not work for a device that has its own key
    shared = client.post("/telemetry", content=body,
                         headers={**_sign("test-shared-secret", body), "X-Device": "bench-01"})
    assert shared.status_code == 401

    db.set_device_active(seeded, "bench-01", False)
    revoked = client.post("/telemetry", content=body,
                          headers={**_sign(key, body), "X-Device": "bench-01"})
    assert revoked.status_code == 401


def test_telemetry_rejects_oversized_bodies(client):
    body = b"x" * 5000
    r = client.post("/telemetry", content=body, headers=_sign("test-shared-secret", body))
    assert r.status_code == 413

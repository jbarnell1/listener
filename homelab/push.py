#!/usr/bin/env python3
"""Web Push (VAPID) to installed PWAs — Android/Chrome (and desktop) (ADR-046).

alerts._notify() calls broadcast() first; if there are no subscribers (or push isn't
set up) it returns False and the caller falls back to email. The VAPID keypair is
generated once and kept in ~/.listener-push/.
"""
import base64
import json
import os

import db

PUSH_DIR = os.path.expanduser("~/.listener-push")
PRIV = os.path.join(PUSH_DIR, "vapid_private.pem")
SUBJECT = "mailto:jonathanbarnell@gmail.com"   # VAPID contact (required by push services)


def _vapid():
    from py_vapid import Vapid01
    if os.path.exists(PRIV):
        return Vapid01.from_file(PRIV)
    os.makedirs(PUSH_DIR, exist_ok=True)
    v = Vapid01()
    v.generate_keys()
    v.save_key(PRIV)
    try:
        os.chmod(PRIV, 0o600)
    except OSError:
        pass
    return v


def public_key():
    """The applicationServerKey the browser needs to subscribe (base64url, uncompressed point)."""
    from cryptography.hazmat.primitives import serialization
    raw = _vapid().public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def configured():
    return bool(db.list_push_subs(db.connect()))


def broadcast(title, body, conn=None):
    """Push to every subscriber; prune dead ones. Returns True if at least one delivered."""
    conn = conn or db.connect()
    subs = db.list_push_subs(conn)
    if not subs:
        return False
    from pywebpush import webpush, WebPushException
    payload = json.dumps({"title": title, "body": body, "url": "/"})
    sent = 0
    for s in subs:
        info = {"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}
        try:
            webpush(subscription_info=info, data=payload,
                    vapid_private_key=PRIV, vapid_claims={"sub": SUBJECT})
            sent += 1
        except WebPushException as e:  # noqa: BLE001
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):                     # subscription expired/unsubscribed
                db.remove_push_sub(conn, s["endpoint"])
            else:
                print(f"push: send failed ({code}): {e}")
        except Exception as e:  # noqa: BLE001
            print(f"push: error: {e}")
    return sent > 0


if __name__ == "__main__":
    import sys
    if "--key" in sys.argv:
        print(public_key())
    elif "--test" in sys.argv:
        print("delivered:", broadcast("🔔 Listener test", "Push is working."))
    else:
        print(f"subscribers: {len(db.list_push_subs(db.connect()))}\npublic key: {public_key()}")

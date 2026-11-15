"""Test setup: every test run gets its own empty database and data folder.

The paths are set BEFORE the app modules are imported, because db.py and app.py read
LISTENER_DB / LISTENER_DATA_DIR at import time. Nothing here touches a real install, the
network, a GPU or Ollama.
"""
import os
import sys
import tempfile

HOMELAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HOMELAB)

_TMP = tempfile.mkdtemp(prefix="listener-test-")
os.environ["LISTENER_DB"] = os.path.join(_TMP, "listener.db")
os.environ["LISTENER_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["LISTENER_INGEST_SECRET"] = "test-shared-secret"
os.environ["HOME"] = _TMP                       # no ~/.listener-gcp, ~/.listener-push
for _k in ("LISTENER_SMTP_USER", "LISTENER_SMTP_PASS", "LISTENER_MAIL_TO", "LISTENER_B2_REMOTE"):
    os.environ.pop(_k, None)

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def seeded():
    """The invented demo data set, written once into the temporary database."""
    import db
    import demo_seed
    conn = db.init_db()
    demo_seed.seed(conn)
    return conn


@pytest.fixture(scope="session")
def client(seeded):
    """The FastAPI app without its lifespan, so no worker, MCP server or scheduler starts."""
    from fastapi.testclient import TestClient

    import app
    return TestClient(app.app)

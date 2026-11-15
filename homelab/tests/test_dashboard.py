"""Every dashboard page renders against the demo data, with and without a path prefix."""
import pytest

PAGES = ["/", "/tasks", "/speakers", "/speakers/2", "/transcripts/1", "/topics", "/topics/1",
         "/topics?t=1", "/timeline", "/unknown", "/activity", "/settings", "/consent",
         "/search?q=coffee", "/healthz", "/queue.json", "/static/manifest.webmanifest"]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders(client, path):
    assert client.get(path).status_code == 200


def test_home_shows_demo_tasks_and_review_queue(client):
    html = client.get("/").text
    assert "Draft the release changelog" in html
    assert "Review queue" in html and "Take over the flaky login test" in html
    assert "Unknown_6" in html                      # an unnamed voice waiting to be named


def test_transcript_is_speaker_attributed(client):
    html = client.get("/transcripts/1").text
    for who in ("Priya", "Alex", "Jordan"):
        assert who in html
    assert "#standup" in html


def test_search_finds_spoken_text(client):
    html = client.get("/search?q=plumber").text
    assert "kitchen tap" in html


def test_export_is_markdown(client):
    r = client.get("/export?ids=2")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "Sam:" in r.text and "oat milk" in r.text


def test_timeline_export_lists_conversations(client):
    body = client.get("/timeline.txt").text
    assert "Conversation #1" in body and "Riley:" in body


def test_missing_things_are_404(client):
    assert client.get("/transcripts/9999").status_code == 404
    assert client.get("/speakers/9999").status_code == 404
    assert client.get("/segment/1/audio.wav").status_code == 404    # demo has no audio


def test_links_carry_the_path_prefix(seeded):
    """Behind a reverse proxy that strips /listener, every in-app link keeps the prefix."""
    from fastapi.testclient import TestClient

    import app
    prefixed = TestClient(app.app, root_path="/listener")
    html = prefixed.get("/").text
    assert 'href="/listener/tasks"' in html
    assert 'href="/tasks"' not in html


def test_dismiss_a_task(client, seeded):
    import db
    before = len(db.list_intents(seeded))
    target = db.list_intents(seeded)[-1]["id"]
    assert client.post(f"/tasks/{target}/dismiss", follow_redirects=False).status_code == 303
    assert len(db.list_intents(seeded)) == before - 1

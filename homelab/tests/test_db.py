"""Pure database helpers."""
import pytest

import db


def test_lipo_percentage_curve():
    assert db.lipo_pct(None) is None
    assert db.lipo_pct(2900) == 0
    assert db.lipo_pct(4200) == 100
    assert 80 <= db.lipo_pct(3950) <= 90
    assert db.lipo_pct(3700) < db.lipo_pct(3850)


def test_demo_seed_refuses_a_database_with_transcripts(seeded):
    import demo_seed
    with pytest.raises(SystemExit):
        demo_seed.seed(seeded)


def test_tags_are_case_insensitive(seeded):
    a = db.get_or_create_tag(seeded, "Groceries")
    b = db.get_or_create_tag(seeded, "groceries")
    assert a == b


def test_time_zone_has_a_value():
    assert db.TZ_NAME            # LISTENER_TZ or the documented default


def test_zz_deleting_a_person_removes_their_lines_and_profile(seeded):
    """Runs last in this file: it removes one of the demo people."""
    riley = seeded.execute("SELECT id FROM speakers WHERE name='Riley'").fetchone()["id"]
    assert db.get_profile(seeded, riley) is not None
    db.delete_speaker(seeded, riley)
    assert db.get_speaker(seeded, riley) is None
    assert db.get_profile(seeded, riley) is None
    assert seeded.execute("SELECT COUNT(*) FROM segments WHERE speaker_id=?",
                          (riley,)).fetchone()[0] == 0

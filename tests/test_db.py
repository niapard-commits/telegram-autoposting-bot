from datetime import datetime, timedelta, timezone

from lutserii.db import StateDB


def test_posted_and_ttl(db):
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert not db.was_posted("a")
    db.mark_posted("a", "deal", now=t0)
    assert db.was_posted("a")
    assert db.was_posted("a", within_days=14, now=t0 + timedelta(days=3))
    assert not db.was_posted("a", within_days=14, now=t0 + timedelta(days=15))


def test_counts(db):
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    db.mark_posted("a", "deal", now=t0)
    db.mark_posted("b", "deal", now=t0 - timedelta(days=2))
    db.mark_posted("c", "free", now=t0)
    assert db.count_kind("deal") == 2
    assert db.count_since("deal", t0 - timedelta(hours=1)) == 1


def test_file_db_persists(tmp_path):
    path = str(tmp_path / "nested" / "state.db")
    first = StateDB(path)
    first.mark_posted("x", "deal")
    first.close()
    second = StateDB(path)
    assert second.was_posted("x")
    second.close()

from datetime import time, date
from src.db import Database

def make(tmp_path):
    return Database(str(tmp_path / "state.db"))

def test_upsert_and_list_target(tmp_path):
    db = make(tmp_path)
    tid = db.upsert_target(
        name="313 Friday", match="CPSC 313", min_seats=1, tiebreak="earliest",
        enabled=True, dry_run=True,
        preferences=[{"location": "ICCS", "time_range": "15:00-17:00", "weekdays": ["Fri"]}],
    )
    rows = db.list_targets()
    assert len(rows) == 1 and rows[0]["name"] == "313 Friday"
    prefs = db.get_preferences(tid)
    assert len(prefs) == 1 and prefs[0]["location"] == "ICCS"

def test_update_replaces_preferences(tmp_path):
    db = make(tmp_path)
    tid = db.upsert_target(name="a", match="X", min_seats=1, tiebreak="earliest",
                           enabled=True, dry_run=True,
                           preferences=[{"location": "ICCS"}])
    db.upsert_target(name="a2", match="Y", min_seats=2, tiebreak="latest",
                     enabled=False, dry_run=False,
                     preferences=[{"location": "HENN"}, {"location": ".*"}],
                     target_id=tid)
    row = db.get_target(tid)
    assert row["name"] == "a2" and row["match"] == "Y" and row["min_seats"] == 2
    assert row["enabled"] == 0 and row["dry_run"] == 0
    assert len(db.get_preferences(tid)) == 2

def test_set_target_flags(tmp_path):
    db = make(tmp_path)
    tid = db.upsert_target(name="a", match="X", min_seats=1, tiebreak="earliest",
                           enabled=True, dry_run=True, preferences=[])
    db.set_target_flags(tid, enabled=False)
    assert db.get_target(tid)["enabled"] == 0
    db.set_target_flags(tid, dry_run=False)
    assert db.get_target(tid)["dry_run"] == 0

def test_delete_target_removes_prefs(tmp_path):
    db = make(tmp_path)
    tid = db.upsert_target(name="a", match="X", min_seats=1, tiebreak="earliest",
                           enabled=True, dry_run=True, preferences=[{"location": "ICCS"}])
    db.delete_target(tid)
    assert db.get_target(tid) is None
    assert db.get_preferences(tid) == []

def test_to_target_exam_maps_to_dataclasses(tmp_path):
    db = make(tmp_path)
    tid = db.upsert_target(
        name="313", match="CPSC 313", min_seats=2, tiebreak="most_seats",
        enabled=True, dry_run=False,
        preferences=[{"location": "ICCS 01[48]", "time_range": "15:00-17:00",
                      "date_range": "2026-10-01..2026-10-03", "weekdays": ["Fri"]}],
    )
    ex = db.to_target_exam(tid)
    assert ex.match.search("CPSC 313 Quiz 1") and ex.min_seats == 2 and ex.tiebreak == "most_seats"
    r = ex.preferences[0]
    assert r.location.search("ICCS 014") and not r.location.search("ICCS 008")
    assert r.time_start == time(15, 0) and r.time_end == time(17, 0)
    assert r.date_start == date(2026, 10, 1) and r.date_end == date(2026, 10, 3)
    assert r.weekdays == {"Fri"}

def test_bookings_dedupe(tmp_path):
    db = make(tmp_path)
    assert db.already_booked(1, "87130") is False
    db.record_booking(1, "87130", "CPSC 313 Quiz 1", "HENN 203",
                      "2026-10-02T13:00:00-07:00", "alice", dry_run=False)
    assert db.already_booked(1, "87130") is True
    assert db.already_booked(1, "999") is False
    assert len(db.recent_bookings()) == 1

def test_dry_run_booking_not_counted_as_booked(tmp_path):
    db = make(tmp_path)
    db.record_booking(1, "87130", "x", "r", "s", "alice", dry_run=True)
    assert db.already_booked(1, "87130") is False  # dry-run doesn't block real booking

def test_events_trim(tmp_path):
    db = make(tmp_path)
    for i in range(520):
        db.add_event("info", f"e{i}")
    ev = db.recent_events(1000)
    assert len(ev) == 500
    assert ev[0]["message"] == "e519"  # newest first

def test_kv(tmp_path):
    db = make(tmp_path)
    assert db.get_kv("session_state", "not_connected") == "not_connected"
    db.set_kv("session_state", "connected")
    assert db.get_kv("session_state") == "connected"

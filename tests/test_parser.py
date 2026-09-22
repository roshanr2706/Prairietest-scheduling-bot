from pathlib import Path
from src.parser import parse_sessions
from src.models import RESERVABLE, NO_SEATS, TIME_LIMIT, NOT_RESERVABLE

HTML = (Path(__file__).parent / "fixtures" / "sessions_fixture.html").read_text(encoding="utf-8")

def test_parses_all_rows_ignoring_delete_modal():
    sessions = parse_sessions(HTML)
    assert len(sessions) == 5  # 5 li rows; modal excluded

def test_statuses_detected():
    s = parse_sessions(HTML)
    assert s[0].status == RESERVABLE
    assert s[1].status == NO_SEATS
    assert s[2].status == TIME_LIMIT
    assert s[3].status == NOT_RESERVABLE
    assert s[4].status == RESERVABLE

def test_reservable_fields():
    s = parse_sessions(HTML)[0]
    assert s.center == "ORCA"
    assert s.room_clean == "HENN 203"
    assert s.available == 4 and s.capacity == 14
    assert s.start.year == 2026 and s.start.hour == 11
    assert s.reserve_button_name.startswith("Reserve this session on Tue, Sep 29, 11am")
    assert "1h 15min" in s.attributes

def test_non_reservable_has_no_button_name():
    s = parse_sessions(HTML)[1]
    assert s.reserve_button_name is None

def test_never_returns_delete_button():
    for s in parse_sessions(HTML):
        assert s.reserve_button_name is None or "Delete" not in s.reserve_button_name

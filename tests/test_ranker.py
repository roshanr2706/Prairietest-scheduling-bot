from datetime import datetime
import re
from src.models import Session, RESERVABLE, NO_SEATS
from src.config import PreferenceRule
from src.ranker import choose_session

def S(room_clean, start, available=5, status=RESERVABLE):
    return Session(status=status, start=start, center="ORCA", room=room_clean,
                   room_clean=room_clean, attributes="", available=available,
                   capacity=50, reserve_button_name=f"Reserve this session on X in ORCA: {room_clean}")

def rule(name, location=None, ts=None, te=None):
    return PreferenceRule(name, re.compile(location, re.I) if location else None,
                          ts, te, None, None, None)

def test_first_matching_rule_wins():
    sessions = [
        S("HENN 203", datetime(2026,10,3,9,0)),
        S("ICCS 014", datetime(2026,10,3,15,0)),
    ]
    rules = [rule("iccs", "ICCS"), rule("any", ".*")]
    chosen = choose_session(sessions, rules, "earliest", 1)
    assert chosen.room_clean == "ICCS 014"

def test_falls_through_to_next_rule():
    sessions = [S("HENN 203", datetime(2026,10,3,9,0))]
    rules = [rule("iccs", "ICCS"), rule("any", ".*")]
    chosen = choose_session(sessions, rules, "earliest", 1)
    assert chosen.room_clean == "HENN 203"

def test_time_range_filter():
    from datetime import time
    sessions = [
        S("ICCS 014", datetime(2026,10,3,9,0)),
        S("ICCS 008", datetime(2026,10,3,16,0)),
    ]
    rules = [PreferenceRule("aft", re.compile("ICCS", re.I), time(15,0), time(17,0), None, None, None)]
    chosen = choose_session(sessions, rules, "earliest", 1)
    assert chosen.room_clean == "ICCS 008"

def test_tiebreak_earliest_vs_latest():
    sessions = [
        S("ICCS 014", datetime(2026,10,3,15,0)),
        S("ICCS 008", datetime(2026,10,3,17,0)),
    ]
    rules = [rule("iccs", "ICCS")]
    assert choose_session(sessions, rules, "earliest", 1).start.hour == 15
    assert choose_session(sessions, rules, "latest", 1).start.hour == 17

def test_tiebreak_most_seats():
    sessions = [
        S("ICCS 014", datetime(2026,10,3,15,0), available=3),
        S("ICCS 008", datetime(2026,10,3,16,0), available=30),
    ]
    rules = [rule("iccs", "ICCS")]
    assert choose_session(sessions, rules, "most_seats", 1).room_clean == "ICCS 008"

def test_min_seats_excludes_low_availability():
    sessions = [S("ICCS 014", datetime(2026,10,3,15,0), available=1)]
    rules = [rule("iccs", "ICCS")]
    assert choose_session(sessions, rules, "earliest", 2) is None

def test_ignores_non_reservable():
    sessions = [S("ICCS 014", datetime(2026,10,3,15,0), status=NO_SEATS)]
    rules = [rule("any", ".*")]
    assert choose_session(sessions, rules, "earliest", 1) is None

def test_no_match_returns_none():
    sessions = [S("HENN 203", datetime(2026,10,3,9,0))]
    rules = [rule("iccs", "ICCS")]
    assert choose_session(sessions, rules, "earliest", 1) is None

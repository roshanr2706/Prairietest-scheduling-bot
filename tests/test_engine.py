import re
from pathlib import Path
from src.config import TargetExam, PreferenceRule
from src.engine import plan_decisions, Decision

FIX = (Path(__file__).parent / "fixtures" / "sessions_fixture.html").read_text(encoding="utf-8")

def texam(match, prefs, tiebreak="earliest", min_seats=1):
    return TargetExam(match=re.compile(match, re.I), exam_id=None,
                      min_seats=min_seats, tiebreak=tiebreak, preferences=prefs)

def rule(location=None):
    return PreferenceRule("r", re.compile(location, re.I) if location else None,
                          None, None, None, None, None)

def test_plan_books_matching_unbooked_exam():
    targets = [(10, texam("CPSC 313", [rule("HENN")]))]
    discovered = {10: [("87130", "CPSC 313 Quiz 1")]}
    pages = {"87130": FIX}
    decisions = plan_decisions(targets, discovered, pages,
                               already_booked=lambda t, e: False,
                               dry_run_by_target={10: True})
    assert len(decisions) == 1
    d = decisions[0]
    assert d.target_id == 10 and d.exam_id == "87130"
    assert d.session.room_clean == "HENN 203"

def test_plan_skips_already_booked():
    targets = [(10, texam("CPSC 313", [rule(".*")]))]
    discovered = {10: [("87130", "CPSC 313 Quiz 1")]}
    decisions = plan_decisions(targets, discovered, {"87130": FIX},
                               already_booked=lambda t, e: True,
                               dry_run_by_target={10: False})
    assert decisions == []

def test_plan_skips_exam_with_no_matching_slot():
    targets = [(10, texam("CPSC 313", [rule("NONEXISTENT ROOM")]))]
    decisions = plan_decisions(targets, {10: [("87130", "x")]}, {"87130": FIX},
                               already_booked=lambda t, e: False,
                               dry_run_by_target={10: False})
    assert decisions == []

def test_plan_multiple_exams_one_target():
    targets = [(10, texam("CPSC 313", [rule("ICCS")]))]
    discovered = {10: [("87130", "Quiz 1"), ("87131", "Quiz 2")]}
    pages = {"87130": FIX, "87131": FIX}
    decisions = plan_decisions(targets, discovered, pages,
                               already_booked=lambda t, e: False,
                               dry_run_by_target={10: True})
    assert {d.exam_id for d in decisions} == {"87130", "87131"}

from datetime import datetime
from src.models import Session, RESERVABLE
from src.engine import booking_message, auth_config_from_env

def test_booking_message_format():
    s = Session(status=RESERVABLE, start=datetime(2026,10,2,13,0), center="ORCA",
                room="💥HENN 203", room_clean="HENN 203", attributes="", available=4,
                capacity=14, reserve_button_name="Reserve this session on X in ORCA: HENN 203")
    msg = booking_message("alice", "CPSC 313 Quiz 1", s)
    assert msg == "alice has had CPSC 313 Quiz 1 booked at 2026-10-02 13:00 in HENN 203"

def test_auth_config_from_env(monkeypatch):
    monkeypatch.setenv("CWL_USERNAME", "bob")
    monkeypatch.setenv("CWL_PASSWORD", "pw")
    monkeypatch.setenv("DUO_WAIT_SECONDS", "90")
    cfg = auth_config_from_env()
    assert cfg.username == "bob" and cfg.password == "pw" and cfg.duo_wait_seconds == 90

from src.engine import next_login_action

def test_next_login_action():
    # Connected -> proceed to watch cycle
    assert next_login_action("connected") == "watch"
    # User clicked Connect -> attempt login (this is what triggers Duo)
    assert next_login_action("connecting") == "login"
    # Not connected / failed / unknown -> wait for the user to click Connect
    assert next_login_action("not_connected") == "wait"
    assert next_login_action("auth_failed") == "wait"
    assert next_login_action("anything_else") == "wait"

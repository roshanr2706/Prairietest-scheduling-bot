import asyncio
from datetime import datetime
from src.models import Session, RESERVABLE
from src.booker import book, BookResult, FAILED, DRY_RUN, is_booking_confirmed

# Real reservation-confirmation page text (captured live 2026-09-22).
CONFIRMED_TEXT = (
    "Exam reservation Exam: CPSC 320 (2026W1): Test 1 Date: Fri, Oct 9, 4pm (PDT) "
    "Location: ORCA: ICCS 014 Change or delete this reservation Take your exam "
    "Your exam is scheduled to start at 4pm Fri, Oct 9 (PDT) in ORCA: ICCS 014."
)

def test_is_booking_confirmed_on_real_reservation_page():
    assert is_booking_confirmed(CONFIRMED_TEXT) is True

def test_is_booking_confirmed_false_on_session_list():
    # Still on the slot-picker (booking did not take) -> not confirmed.
    assert is_booking_confirmed("Choose a session for CPSC 320 Reserve this session") is False
    assert is_booking_confirmed("") is False

def mk(name):
    return Session(status=RESERVABLE, start=datetime(2026,10,3,15,0), center="ORCA",
                   room="ICCS 008", room_clean="ICCS 008", attributes="", available=5,
                   capacity=44, reserve_button_name=name)

class FakePage:
    """Minimal stand-in; book() must not touch it in the guarded/dry paths under test."""
    def get_by_role(self, *a, **k):
        raise AssertionError("should not be called in these tests")

def test_refuses_missing_button_name():
    res = asyncio.run(book(FakePage(), mk(None), dry_run=True))
    assert res.outcome == FAILED

def test_refuses_non_reserve_name():
    res = asyncio.run(book(FakePage(), mk("Delete reservation"), dry_run=True))
    assert res.outcome == FAILED

def test_dry_run_does_not_click():
    res = asyncio.run(book(FakePage(), mk("Reserve this session on Sat, Oct 3, 3pm (PDT) in ORCA: ⛳️ICCS 008"), dry_run=True))
    assert res.outcome == DRY_RUN
    assert "ICCS 008" in res.detail

import asyncio
from datetime import datetime
from src.models import Session, RESERVABLE
from src.booker import book, BookResult, FAILED, DRY_RUN

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

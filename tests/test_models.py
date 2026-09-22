from datetime import datetime
from src.models import Session, clean_room, RESERVABLE

def test_clean_room_strips_leading_emoji_and_space():
    assert clean_room("💥HENN 203") == "HENN 203"
    assert clean_room("🎯ICCS 014") == "ICCS 014"
    assert clean_room("  ⛳️ICCS 008 ") == "ICCS 008"
    assert clean_room("BUCH B101") == "BUCH B101"

def test_session_construction():
    s = Session(
        status=RESERVABLE,
        start=datetime(2026, 9, 29, 11, 0),
        center="ORCA", room="💥HENN 203", room_clean="HENN 203",
        attributes="1h 15min, In-person, CfA, 150% time",
        available=4, capacity=14,
        reserve_button_name="Reserve this session on Tue, Sep 29, 11am (PDT) in ORCA: 💥HENN 203",
    )
    assert s.available == 4 and s.room_clean == "HENN 203"

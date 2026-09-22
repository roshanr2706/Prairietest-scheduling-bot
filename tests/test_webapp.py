import pytest
from fastapi.testclient import TestClient
from src.db import Database
from src.webapp import create_app, check_cwl

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CWL_USERNAME", "alice")
    monkeypatch.setenv("CWL_PASSWORD", "pw")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("DISABLE_ENGINE", "1")
    db = Database(str(tmp_path / "state.db"))
    app = create_app(db=db)
    return TestClient(app), db

def test_check_cwl(monkeypatch):
    monkeypatch.setenv("CWL_USERNAME", "alice")
    monkeypatch.setenv("CWL_PASSWORD", "pw")
    assert check_cwl("alice", "pw") is True
    assert check_cwl("alice", "wrong") is False
    assert check_cwl("bob", "pw") is False

def test_dashboard_requires_login(client):
    c, _ = client
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"].endswith("/login")

def test_login_rejects_bad_creds(client):
    c, _ = client
    r = c.post("/login", data={"username": "alice", "password": "nope"}, follow_redirects=False)
    assert r.status_code == 200  # re-renders login with error
    assert "Invalid" in r.text

def test_login_then_dashboard(client):
    c, _ = client
    r = c.post("/login", data={"username": "alice", "password": "pw"}, follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    r2 = c.get("/")
    assert r2.status_code == 200

def login(c):
    c.post("/login", data={"username": "alice", "password": "pw"})

def test_create_and_list_target(client):
    c, db = client
    login(c)
    r = c.post("/targets", data={
        "name": "313 Friday", "match": "CPSC 313", "min_seats": "1",
        "tiebreak": "earliest", "enabled": "on", "dry_run": "on",
        "pref_location": ["ICCS", ""], "pref_time_range": ["15:00-17:00", ""],
        "pref_date_range": ["", ""], "pref_weekdays": ["Fri", ""],
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    rows = db.list_targets()
    assert len(rows) == 1 and rows[0]["name"] == "313 Friday"
    assert len(db.get_preferences(rows[0]["id"])) == 1  # blank row ignored
    body = c.get("/").text
    assert "313 Friday" in body

def test_toggle_and_delete(client):
    c, db = client
    login(c)
    tid = db.upsert_target(name="a", match="X", min_seats=1, tiebreak="earliest",
                           enabled=True, dry_run=True, preferences=[])
    c.post(f"/targets/{tid}/toggle", data={"field": "dry_run"})
    assert db.get_target(tid)["dry_run"] == 0
    c.post(f"/targets/{tid}/delete")
    assert db.get_target(tid) is None

def test_watch_start_stop_and_status(client):
    c, db = client
    login(c)
    c.post("/watch/stop")
    assert db.get_kv("watch_running") == "0"
    c.post("/watch/start")
    assert db.get_kv("watch_running") == "1"
    s = c.get("/api/status").json()
    assert s["watch_running"] is True and "session_state" in s

def test_events_api(client):
    c, db = client
    login(c)
    db.add_event("info", "hello world")
    data = c.get("/api/events").json()
    assert any(e["message"] == "hello world" for e in data["events"])

def test_secure_cookies_toggle(tmp_path, monkeypatch):
    monkeypatch.setenv("CWL_USERNAME", "alice")
    monkeypatch.setenv("CWL_PASSWORD", "pw")
    monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.setenv("DISABLE_ENGINE", "1")
    monkeypatch.setenv("SECURE_COOKIES", "1")
    app = create_app(db=Database(str(tmp_path / "s.db")))
    c = TestClient(app)
    r = c.post("/login", data={"username": "alice", "password": "pw"}, follow_redirects=False)
    assert "secure" in r.headers.get("set-cookie", "").lower()

def test_default_cookie_not_secure(client):
    # Default (no SECURE_COOKIES) keeps the cookie usable over plain HTTP.
    c, _ = client
    r = c.post("/login", data={"username": "alice", "password": "pw"}, follow_redirects=False)
    assert "secure" not in r.headers.get("set-cookie", "").lower()

def test_scan_now_sets_flag(client):
    c, db = client
    login(c)
    assert db.get_kv("scan_now", "0") == "0"
    c.post("/scan")
    assert db.get_kv("scan_now") == "1"

def test_connect_sets_connecting(client):
    # Connect must flip state to 'connecting' — that is what the engine picks up
    # to perform the login + Duo push (see engine.next_login_action).
    c, db = client
    login(c)
    c.post("/connect")
    assert db.get_kv("session_state") == "connecting"

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

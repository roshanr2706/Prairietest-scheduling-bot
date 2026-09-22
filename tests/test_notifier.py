from src import notifier as N

class FakeResp:
    status_code = 204

def test_no_webhook_returns_false(caplog):
    n = N.Notifier(None)
    assert n.send("started", "hi") is False

def test_discord_payload(monkeypatch):
    captured = {}
    def fake_post(url, json, timeout):
        captured["url"] = url; captured["json"] = json; return FakeResp()
    monkeypatch.setattr(N.requests, "post", fake_post)
    n = N.Notifier("https://discord.com/api/webhooks/xyz")
    assert n.send("booked", "Got HENN 203") is True
    assert captured["json"] == {"content": "[booked] Got HENN 203"}

def test_slack_payload(monkeypatch):
    captured = {}
    def fake_post(url, json, timeout):
        captured["json"] = json; return FakeResp()
    monkeypatch.setattr(N.requests, "post", fake_post)
    n = N.Notifier("https://hooks.slack.com/services/xyz")
    assert n.send("booked", "Got HENN 203") is True
    assert captured["json"] == {"text": "[booked] Got HENN 203"}

def test_network_error_returns_false(monkeypatch):
    def boom(*a, **k):
        raise N.requests.RequestException("down")
    monkeypatch.setattr(N.requests, "post", boom)
    n = N.Notifier("https://hooks.slack.com/services/xyz")
    assert n.send("crash", "oops") is False

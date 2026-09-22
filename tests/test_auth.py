import asyncio
import pytest
from src.config import AuthConfig
from src import auth as A

def cfg(username="alice", password="pw"):
    return AuthConfig(username=username, password=password, duo_wait_seconds=5, trust_device=True)

def test_credentials_returns_pair():
    assert A.credentials(cfg()) == ("alice", "pw")

def test_credentials_raises_when_missing():
    with pytest.raises(A.AuthError):
        A.credentials(cfg(username=None))
    with pytest.raises(A.AuthError):
        A.credentials(cfg(password=None))

def test_session_exists(tmp_path):
    p = tmp_path / "storageState.json"
    assert A.session_exists(str(p)) is False
    p.write_text("{}", encoding="utf-8")
    assert A.session_exists(str(p)) is True

def test_is_login_page():
    assert A.is_login_page("https://authentication.ubc.ca/cas/login") is True
    assert A.is_login_page("https://api-2fa.duosecurity.com/frame") is True
    assert A.is_login_page("https://us.prairietest.com/pt/student/exam/1") is False

class FakeLocator:
    def __init__(self, count):
        self._count = count
        self.filled = None
        self.clicked = False
    async def count(self):
        return self._count
    async def fill(self, value):
        self.filled = value
    async def is_visible(self):
        return self._count > 0
    @property
    def first(self):
        return self

class FakePage:
    """Records locator lookups; the second selector 'hits' (count=1)."""
    def __init__(self, hit_selector):
        self.hit_selector = hit_selector
        self.lookups = []
    def locator(self, selector):
        self.lookups.append(selector)
        return FakeLocator(1 if selector == self.hit_selector else 0)

def test_fill_first_tries_in_order_until_hit():
    page = FakePage(hit_selector=A.PASSWORD_SELECTORS[1])
    ok = asyncio.run(A._fill_first(page, A.PASSWORD_SELECTORS, "s3cret"))
    assert ok is True
    # tried the first (miss) then the second (hit); stopped there
    assert page.lookups[:2] == A.PASSWORD_SELECTORS[:2]

def test_fill_first_returns_false_when_none_match():
    page = FakePage(hit_selector="#nope")
    ok = asyncio.run(A._fill_first(page, A.PASSWORD_SELECTORS, "x"))
    assert ok is False

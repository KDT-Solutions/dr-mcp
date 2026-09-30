"""Gemeinsame Fixtures. Alle HTTP-Requests laufen gegen respx-Mocks, nie gegen die echte API."""

import os
import sys

import pytest
import respx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

BASE_URL = "https://api.example.com/dr"
TOKEN_URL = f"{BASE_URL}/oauth/token"
CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret-value"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DR_BASE_URL", BASE_URL)
    monkeypatch.setenv("DR_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("DR_CLIENT_SECRET", CLIENT_SECRET)
    for name in ("DR_TOKEN_URL", "DR_SCOPE", "DR_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def server(env):
    from dr_mcp import server as srv

    srv._client = None
    yield srv
    srv._client = None


@pytest.fixture
def api():
    # assert_all_mocked: jeder nicht gemockte Request schlägt fehl -> kein Zugriff auf echte APIs.
    with respx.mock(base_url=BASE_URL, assert_all_called=False, assert_all_mocked=True) as mock:
        mock.post("/oauth/token").respond(
            200, json={"access_token": "tok-1", "token_type": "bearer", "expires_in": 3600, "scope": "read write"}
        )
        yield mock

"""Tests für den Schutz des MCP-HTTP-Endpunkts (Bearer-Token, fail-safe Start, CORS)."""

import pytest
from starlette.testclient import TestClient

API_KEY = "test-mcp-api-key"


@pytest.fixture
def http_client(server):
    app = server._build_http_app(API_KEY)
    with TestClient(app, base_url="https://mcp.example.com") as client:
        yield client


def init_request():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }


HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_http_mode_refuses_start_without_api_key(server, monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MCP_API_KEY"):
        server.main()
    with pytest.raises(RuntimeError, match="MCP_API_KEY"):
        server._build_http_app("")


def test_request_without_token_is_401(http_client):
    resp = http_client.post("/mcp", json=init_request(), headers=HEADERS)
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


@pytest.mark.parametrize("auth", ["Bearer wrong", f"bearer {API_KEY}", API_KEY, f"Bearer {API_KEY}x", "Basic abc"])
def test_wrong_token_is_401(http_client, auth):
    resp = http_client.post("/mcp", json=init_request(), headers={**HEADERS, "Authorization": auth})
    assert resp.status_code == 401


def test_valid_token_passes(http_client):
    resp = http_client.post("/mcp", json=init_request(), headers={**HEADERS, "Authorization": f"Bearer {API_KEY}"})
    assert resp.status_code == 200
    assert resp.json()["result"]["serverInfo"]["name"] == "dr-mcp"


def test_cors_preflight_without_token(http_client):
    resp = http_client.options(
        "/mcp",
        headers={
            "Origin": "https://client.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    assert "access-control-allow-origin" in resp.headers

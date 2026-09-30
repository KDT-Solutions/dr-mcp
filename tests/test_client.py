"""Tests für den API-Client: Token-Handling, Fehlerbehandlung, SimpleResponse NOK."""

import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from conftest import BASE_URL, CLIENT_ID, CLIENT_SECRET
from dr_mcp.client import DigitalRepublicClient, DigitalRepublicError


def make_client(**kw):
    return DigitalRepublicClient(BASE_URL, CLIENT_ID, CLIENT_SECRET, **kw)


async def test_token_request_sends_credentials_in_body(api):
    route = api.get("/api/v1/sims").respond(200, json=[])
    c = make_client()
    await c.list_sims()
    token_req = api.routes[0].calls.last.request
    form = parse_qs(token_req.content.decode())
    assert form == {
        "grant_type": ["client_credentials"],
        "client_id": [CLIENT_ID],
        "client_secret": [CLIENT_SECRET],
    }
    assert "authorization" not in token_req.headers
    assert route.calls.last.request.headers["authorization"] == "Bearer tok-1"


async def test_scope_only_sent_when_configured(api):
    api.get("/api/v1/sims").respond(200, json=[])
    c = make_client(scope="read write")
    await c.list_sims()
    form = parse_qs(api.routes[0].calls.last.request.content.decode())
    assert form["scope"] == ["read write"]


async def test_token_is_cached(api):
    api.get("/api/v1/sims").respond(200, json=[])
    c = make_client()
    await c.list_sims()
    await c.list_sims()
    assert api.routes[0].call_count == 1


async def test_expired_token_is_refreshed(api, monkeypatch):
    api.get("/api/v1/sims").respond(200, json=[])
    c = make_client()
    await c.list_sims()
    c._token_expires_at = 0  # Ablauf simulieren
    await c.list_sims()
    assert api.routes[0].call_count == 2


async def test_401_refreshes_token_once_and_retries(api):
    api.routes[0].side_effect = [
        httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600}),
        httpx.Response(200, json={"access_token": "tok-2", "expires_in": 3600}),
    ]
    route = api.get("/api/v1/sims/8941000000000000001").mock(
        side_effect=[
            httpx.Response(401, json={"error": "invalid_token", "error_description": "Invalid access token: tok-1"}),
            httpx.Response(200, json={"iccid": "8941000000000000001"}),
        ]
    )
    c = make_client()
    result = await c.get_sim("8941000000000000001")
    assert result == {"iccid": "8941000000000000001"}
    assert route.calls[0].request.headers["authorization"] == "Bearer tok-1"
    assert route.calls[1].request.headers["authorization"] == "Bearer tok-2"


async def test_second_401_raises_and_redacts_token(api):
    api.get("/api/v1/sims").respond(
        401, json={"error": "invalid_token", "error_description": "Invalid access token: tok-1"}
    )
    c = make_client()
    with pytest.raises(DigitalRepublicError) as exc:
        await c.list_sims()
    assert exc.value.status_code == 401
    assert "tok-1" not in str(exc.value)
    assert api.routes[1].call_count == 2  # genau ein Retry


async def test_token_error_is_reported_without_secret(api):
    api.routes[0].respond(
        401,
        json={"error": "invalid_client", "error_description": f"Bad client credentials {CLIENT_SECRET}"},
    )
    c = make_client()
    with pytest.raises(DigitalRepublicError) as exc:
        await c.list_sims()
    msg = str(exc.value)
    assert "HTTP 401" in msg and "Bad client credentials" in msg
    assert CLIENT_SECRET not in msg


async def test_token_redirect_is_error(api):
    api.routes[0].respond(302, headers={"location": "/dr/system/500internalServerError"})
    c = make_client()
    with pytest.raises(DigitalRepublicError, match="Weiterleitung"):
        await c.list_sims()


async def test_token_response_without_access_token(api):
    api.routes[0].respond(200, json={"foo": "bar"})
    with pytest.raises(DigitalRepublicError, match="access_token"):
        await make_client().list_sims()


async def test_nok_simple_response_is_error(api):
    api.put("/api/v1/sims/123").respond(
        200,
        json={
            "status": "NOK",
            "message": "Alias ungültig",
            "errorMessages": ["zu lang"],
            "errors": [{"code": 42, "message": "validation failed"}],
        },
    )
    with pytest.raises(DigitalRepublicError) as exc:
        await make_client().rename_sim("123", "x")
    msg = str(exc.value)
    assert "NOK" in msg and "Alias ungültig" in msg and "zu lang" in msg and "[42] validation failed" in msg


async def test_http_400_simple_response_passes_api_text(api):
    api.post("/api/v1/sims/register").respond(400, json={"status": "NOK", "message": "SIM bereits aktiv"})
    with pytest.raises(DigitalRepublicError) as exc:
        await make_client().activate_sim({"iccid": "1", "sim_alias": "a"})
    assert exc.value.status_code == 400
    assert "SIM bereits aktiv" in str(exc.value)


async def test_404_without_body(api):
    api.get("/api/v1/sims/orders/99").respond(404)
    with pytest.raises(DigitalRepublicError, match="HTTP 404.*nicht gefunden"):
        await make_client().get_sim_by_order("99")


async def test_ok_simple_response_returned(api):
    api.post("/api/v1/sims/esim").respond(200, json={"status": "OK", "order_id": 777})
    result = await make_client().activate_esim({"product_id": 5, "sim_alias": "a"})
    assert result == {"status": "OK", "order_id": 777}


async def test_timeout_is_clean_error(api):
    api.get("/api/v1/sims").mock(side_effect=httpx.ReadTimeout("boom"))
    with pytest.raises(DigitalRepublicError, match="Zeitüberschreitung"):
        await make_client().list_sims()


async def test_connect_error_is_clean_error(api):
    api.get("/api/v1/sims").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(DigitalRepublicError, match="Verbindungsfehler"):
        await make_client().list_sims()


async def test_html_error_page_not_passed_through(api):
    api.get("/api/v1/sims").respond(500, text="<html><body>stack trace</body></html>", headers={"content-type": "text/html"})
    with pytest.raises(DigitalRepublicError) as exc:
        await make_client().list_sims()
    assert str(exc.value) == "API-Fehler (HTTP 500)"


async def test_list_sims_query_params(api):
    route = api.get("/api/v1/sims").respond(200, json=[])
    await make_client().list_sims(search="abc", status=None, page=2, pageSize=10)
    params = dict(route.calls.last.request.url.params)
    assert params == {"search": "abc", "page": "2", "pageSize": "10"}


async def test_available_subscriptions_esim_vs_sim(api):
    esim = api.get("/api/v1/sims/available_subscriptions").respond(200, json={"available_subscriptions": []})
    sim = api.get("/api/v1/sims/123/available_subscriptions").respond(200, json={"available_subscriptions": []})
    c = make_client()
    await c.list_available_subscriptions()
    await c.list_available_subscriptions("123")
    assert esim.call_count == 1 and sim.call_count == 1


async def test_network_reset_body(api):
    route = api.post("/api/v1/sims/networkreset").respond(200, json={"status": "OK"})
    await make_client().network_reset("123")
    assert json.loads(route.calls.last.request.content) == {"iccid": "123"}

"""Tests für die MCP-Tools: Validierung, confirm-Pflicht, Weitergabe an die API."""

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

ICCID = "8941000000000000001"


async def call(server, name, **args):
    """Tool über das MCP-Server-Objekt aufrufen (inkl. Argument-Validierung des SDK)."""
    return await server.mcp.call_tool(name, args)


def result_json(result):
    return json.loads(result.content[0].text)


# -- Validierung ---------------------------------------------------------------


@pytest.mark.parametrize("iccid", ["", "  ", "8941abc", "8941-0000", "８９４１"])
async def test_invalid_iccid_rejected_without_request(server, api, iccid):
    with pytest.raises(ToolError, match="iccid"):
        await call(server, "get_sim", iccid=iccid)
    assert api.calls.call_count == 0


async def test_alias_too_long_rename(server, api):
    with pytest.raises(ToolError, match="maximal 24"):
        await call(server, "rename_sim", iccid=ICCID, sim_alias="x" * 25, confirm=True)
    assert api.calls.call_count == 0


async def test_alias_max_length_ok(server, api):
    api.put(f"/api/v1/sims/{ICCID}").respond(200, json={"status": "OK"})
    await call(server, "rename_sim", iccid=ICCID, sim_alias="x" * 24, confirm=True)


async def test_esim_alias_limit_12(server, api):
    with pytest.raises(ToolError, match="maximal 12"):
        await call(server, "activate_esim", product_id=1, sim_alias="x" * 13, confirm=True)
    assert api.calls.call_count == 0


async def test_invalid_subscription_type(server, api):
    with pytest.raises(ToolError, match="Data_CH, Data_Roaming"):
        await call(server, "modify_subscription", iccid=ICCID, type="Voice", product_id=1, confirm=True)
    assert api.calls.call_count == 0


@pytest.mark.parametrize(
    "args,msg",
    [
        ({"status": "active"}, "status"),
        ({"sort_field": "foo"}, "sort_field"),
        ({"sort_direction": "up"}, "sort_direction"),
        ({"page": 0}, "page"),
        ({"page_size": -1}, "page_size"),
    ],
)
async def test_list_sims_invalid_params(server, api, args, msg):
    with pytest.raises(ToolError, match=msg):
        await call(server, "list_sims", **args)
    assert api.calls.call_count == 0


# -- confirm-Pflicht -------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,args",
    [
        ("rename_sim", {"iccid": ICCID, "sim_alias": "Test"}),
        ("modify_subscription", {"iccid": ICCID, "type": "Data_CH", "product_id": 5}),
        ("activate_sim", {"iccid": ICCID, "sim_alias": "Test"}),
        ("activate_esim", {"product_id": 5, "sim_alias": "Test"}),
        ("network_reset", {"iccid": ICCID}),
    ],
)
@pytest.mark.parametrize("confirm", [None, False])
async def test_write_tools_require_confirm(server, api, tool, args, confirm):
    if confirm is not None:
        args = {**args, "confirm": confirm}
    with pytest.raises(ToolError, match="confirm=true"):
        await call(server, tool, **args)
    assert api.calls.call_count == 0


async def test_cost_warning_in_refusal(server, api):
    with pytest.raises(ToolError, match="Kosten"):
        await call(server, "activate_sim", iccid=ICCID, sim_alias="Test")


# -- Erfolgsfälle / Payloads -----------------------------------------------------


async def test_list_sims_maps_params(server, api):
    route = api.get("/api/v1/sims").respond(200, json=[{"iccid": ICCID}])
    await call(
        server,
        "list_sims",
        search="Test",
        status="subscription_active",
        page=2,
        page_size=20,
        sort_field="sim_alias",
        sort_direction="desc",
    )
    assert dict(route.calls.last.request.url.params) == {
        "search": "Test",
        "status": "subscription_active",
        "page": "2",
        "pageSize": "20",
        "sortField": "sim_alias",
        "sortDirection": "desc",
    }


async def test_get_sim_history(server, api):
    route = api.get(f"/api/v1/sims/{ICCID}").respond(200, json={"iccid": ICCID})
    await call(server, "get_sim", iccid=f" {ICCID} ", history=True)
    assert route.calls.last.request.url.params["history"] == "true"


async def test_get_sim_by_order_accepts_int(server, api):
    route = api.get("/api/v1/sims/orders/12345").respond(200, json={"iccid": ICCID})
    await call(server, "get_sim_by_order", order_id=12345)
    assert route.call_count == 1


async def test_order_id_is_url_encoded(server, api):
    route = api.get("/api/v1/sims/orders/a%2Fb").respond(200, json={})
    await call(server, "get_sim_by_order", order_id="a/b")
    assert route.call_count == 1


async def test_modify_subscription_payload(server, api):
    route = api.put(f"/api/v1/sims/{ICCID}/subscriptions").respond(200, json={"status": "OK", "order_id": 1})
    await call(server, "modify_subscription", iccid=ICCID, type="Data_Roaming", product_id=7, autorenew=True, confirm=True)
    assert json.loads(route.calls.last.request.content) == {"type": "Data_Roaming", "product_id": 7, "autorenew": True}


async def test_modify_subscription_without_autorenew(server, api):
    route = api.put(f"/api/v1/sims/{ICCID}/subscriptions").respond(200, json={"status": "OK"})
    await call(server, "modify_subscription", iccid=ICCID, type="Data_CH", product_id=-1, confirm=True)
    assert json.loads(route.calls.last.request.content) == {"type": "Data_CH", "product_id": -1}


async def test_activate_sim_payload(server, api):
    route = api.post("/api/v1/sims/register").respond(200, json={"status": "OK", "order_id": 9})
    result = await call(server, "activate_sim", iccid=ICCID, sim_alias="Lager 1", autorenew=False, confirm=True)
    assert json.loads(route.calls.last.request.content) == {"iccid": ICCID, "sim_alias": "Lager 1", "autorenew": False}
    assert result_json(result)["order_id"] == 9


async def test_activate_esim_payload(server, api):
    route = api.post("/api/v1/sims/esim").respond(200, json={"status": "OK", "order_id": 10})
    await call(server, "activate_esim", product_id=3, sim_alias="Tablet", confirm=True)
    assert json.loads(route.calls.last.request.content) == {"product_id": 3, "sim_alias": "Tablet"}


async def test_nok_becomes_tool_error(server, api):
    api.post("/api/v1/sims/networkreset").respond(200, json={"status": "NOK", "message": "Reset nicht möglich"})
    with pytest.raises(ToolError, match="NOK: Reset nicht möglich"):
        await call(server, "network_reset", iccid=ICCID, confirm=True)


async def test_list_available_subscriptions_without_iccid_uses_esim_endpoint(server, api):
    route = api.get("/api/v1/sims/available_subscriptions").respond(200, json={"available_subscriptions": []})
    await call(server, "list_available_subscriptions")
    await call(server, "list_available_subscriptions", iccid="")
    assert route.call_count == 2


async def test_missing_config(server, api, monkeypatch):
    monkeypatch.delenv("DR_CLIENT_SECRET")
    with pytest.raises(ToolError, match="DR_CLIENT_SECRET"):
        await call(server, "list_sims")


async def test_get_version(server):
    result = await call(server, "get_version")
    data = result_json(result)
    assert data["name"] == "dr-mcp" and data["version"]

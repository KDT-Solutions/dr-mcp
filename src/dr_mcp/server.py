"""
dr-mcp - MCP-Server für die Digital Republic API (SIM-/eSIM-Verwaltung).

Konfiguration (ausschliesslich über Umgebungsvariablen):
  DR_BASE_URL        - Pflicht. Basis-URL der API (servers-Eintrag der OpenAPI-Spec).
  DR_CLIENT_ID       - Pflicht. OAuth2-Client-ID.
  DR_CLIENT_SECRET   - Pflicht. OAuth2-Client-Secret.
  DR_TOKEN_URL       - optional. Standard: <DR_BASE_URL>/oauth/token (laut Spec).
  DR_SCOPE           - optional. Wird nur mitgeschickt, wenn gesetzt (z.B. "read write").
  DR_TIMEOUT         - optional. HTTP-Timeout in Sekunden (Standard 30).

  MCP_TRANSPORT      - "stdio" (Standard, lokal) oder "http" (Cloud/Docker).
  MCP_HOST           - Bind-Adresse im HTTP-Modus (Standard 0.0.0.0).
  MCP_PORT           - Port im HTTP-Modus (Standard 8000).
  MCP_API_KEY        - Pflicht bei MCP_TRANSPORT=http. Statisches Bearer-Token;
                       jeder Request muss "Authorization: Bearer <token>" senden.
                       Ohne MCP_API_KEY startet der HTTP-Modus nicht - gleiches
                       Muster wie plesk-mcp/also-mcp/zammad-mcp.

Jedes schreibende Tool verlangt confirm=true (wie die DNS-Tools im plesk-mcp).
Ohne confirm=true wird nichts an die API gesendet; die Antwort beschreibt,
was ausgeführt würde. Besonders wichtig bei activate_sim, activate_esim und
modify_subscription, da diese Abos und damit Kosten auslösen.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import validation as v
from .client import DigitalRepublicClient, DigitalRepublicError

# Logs immer nach stderr - stdout gehört im stdio-Modus dem MCP-Protokoll.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx loggt sonst jede URL auf INFO - unkritisch, aber unnötig doppelt.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("dr_mcp")


def _read_version() -> str:
    """APP_VERSION (vom Docker-Build gesetzt), sonst Version aus pyproject.toml/Paket-Metadaten."""
    env_version = os.environ.get("APP_VERSION", "").strip()
    if env_version:
        return env_version
    try:
        import tomllib

        pyproject = os.path.join(os.path.dirname(__file__), "..", "..", "pyproject.toml")
        with open(pyproject, "rb") as f:
            return tomllib.load(f)["project"]["version"]
    except Exception:
        pass
    try:
        from importlib.metadata import version

        return version("dr-mcp")
    except Exception:
        return "0.0.0-dev"


__version__ = _read_version()

mcp = MCPServer("dr-mcp", version=__version__)

_client: DigitalRepublicClient | None = None


def _get_client() -> DigitalRepublicClient:
    global _client
    if _client is None:
        base_url = os.environ.get("DR_BASE_URL", "").strip()
        client_id = os.environ.get("DR_CLIENT_ID", "").strip()
        client_secret = os.environ.get("DR_CLIENT_SECRET", "")
        missing = [
            name
            for name, val in (("DR_BASE_URL", base_url), ("DR_CLIENT_ID", client_id), ("DR_CLIENT_SECRET", client_secret))
            if not val
        ]
        if missing:
            raise ToolError(f"Konfiguration unvollständig, fehlende Umgebungsvariablen: {', '.join(missing)}")
        try:
            timeout = float(os.environ.get("DR_TIMEOUT", "30"))
        except ValueError:
            raise ToolError("DR_TIMEOUT muss eine Zahl (Sekunden) sein") from None
        _client = DigitalRepublicClient(
            base_url,
            client_id,
            client_secret,
            token_url=os.environ.get("DR_TOKEN_URL", "").strip() or None,
            scope=os.environ.get("DR_SCOPE", "").strip() or None,
            timeout=timeout,
        )
    return _client


async def _call(coro_factory) -> Any:
    """API-Aufruf ausführen, Fehler als saubere ToolError (ohne Stacktrace) zurückgeben."""
    client = _get_client()
    try:
        return await coro_factory(client)
    except DigitalRepublicError as exc:
        raise ToolError(str(exc)) from None


def _validated(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise ToolError(f"Ungültige Eingabe: {exc}") from None


def _require_confirm(confirm: bool, action: str) -> None:
    if confirm is not True:
        raise ToolError(
            f"Abgelehnt - nichts ausgeführt. {action} ist eine schreibende Aktion. "
            "Zum Ausführen erneut mit confirm=true aufrufen."
        )


def _payload_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Lesende Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_version() -> dict[str, str]:
    """Version des laufenden dr-mcp-Servers abfragen (Redeploy-Kontrolle)."""
    return {"name": "dr-mcp", "version": __version__, "commit": os.environ.get("GIT_SHA", "unbekannt")}


@mcp.tool()
async def list_sims(
    search: str | None = None,
    status: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    sort_field: str | None = None,
    sort_direction: str | None = None,
) -> Any:
    """SIM-Karten des Kunden auflisten (GET /api/v1/sims).

    search: Suchbegriff für sim_alias, iccid, imsi oder msisdn.
    status: subscription_active | no_subscription_active | no_data_package_active
    page: Seitennummer ab 1 (API-Standard 1).
    page_size: Einträge pro Seite (API-Standard 8).
    sort_field: contract_nr | iccid | imsi | msisdn | provider | sim_alias | subscription_status
        (API-Standard iccid)
    sort_direction: asc | desc (API-Standard asc)
    """
    if status is not None:
        _validated(v.validate_choice, status, v.SIM_STATUS_VALUES, "status")
    if sort_field is not None:
        _validated(v.validate_choice, sort_field, v.SORT_FIELDS, "sort_field")
    if sort_direction is not None:
        _validated(v.validate_choice, sort_direction, v.SORT_DIRECTIONS, "sort_direction")
    if page is not None:
        _validated(v.validate_positive_int, page, "page")
    if page_size is not None:
        _validated(v.validate_positive_int, page_size, "page_size")
    params = {
        "search": search,
        "status": status,
        "page": page,
        "pageSize": page_size,
        "sortField": sort_field,
        "sortDirection": sort_direction,
    }
    return await _call(lambda c: c.list_sims(**params))


@mcp.tool()
async def get_sim(iccid: str, history: bool = False) -> Any:
    """Details einer SIM-Karte abrufen (GET /api/v1/sims/{iccid}).

    iccid: ICCID der SIM-Karte (nur Ziffern).
    history: true = zusätzlich die Abo-Historie (subscription_history) liefern.
    """
    iccid = _validated(v.validate_iccid, iccid)
    return await _call(lambda c: c.get_sim(iccid, history=history))


@mcp.tool()
async def get_sim_by_order(order_id: str | int) -> Any:
    """SIM-Karte zu einer Auftragsnummer abrufen (GET /api/v1/sims/orders/{order_id}).

    Die order_id wird z.B. von activate_sim, activate_esim oder
    modify_subscription in der Antwort zurückgegeben.
    """
    order_id = _validated(v.validate_order_id, str(order_id))
    return await _call(lambda c: c.get_sim_by_order(order_id))


@mcp.tool()
async def list_available_subscriptions(iccid: str | None = None) -> Any:
    """Buchbare Abos/Datenpakete auflisten.

    Mit iccid: Abos für diese SIM (GET /api/v1/sims/{iccid}/available_subscriptions).
    Ohne iccid: Abos für eine neue eSIM (GET /api/v1/sims/available_subscriptions) -
    die productId daraus wird für activate_esim gebraucht.
    """
    if iccid is not None and iccid.strip():
        iccid = _validated(v.validate_iccid, iccid)
    else:
        iccid = None
    return await _call(lambda c: c.list_available_subscriptions(iccid))


# ---------------------------------------------------------------------------
# Schreibende Tools (alle mit confirm=true)
# ---------------------------------------------------------------------------


@mcp.tool()
async def rename_sim(iccid: str, sim_alias: str, confirm: bool = False) -> Any:
    """Alias (Anzeigename) einer SIM-Karte ändern (PUT /api/v1/sims/{iccid}).

    sim_alias: neuer Alias, maximal 24 Zeichen.
    Schreibende Aktion: erfordert confirm=true.
    """
    iccid = _validated(v.validate_iccid, iccid)
    sim_alias = _validated(v.validate_alias, sim_alias, v.SIM_ALIAS_MAX)
    _require_confirm(confirm, f"Alias der SIM {iccid} auf {sim_alias!r} setzen")
    return await _call(lambda c: c.rename_sim(iccid, sim_alias))


@mcp.tool()
async def modify_subscription(
    iccid: str,
    type: str,
    product_id: int,
    autorenew: bool | None = None,
    confirm: bool = False,
) -> Any:
    """Abo/Datenpaket einer SIM-Karte ändern (PUT /api/v1/sims/{iccid}/subscriptions).

    ACHTUNG: kann kostenpflichtige Abos auslösen.
    type: Data_CH | Data_Roaming
    product_id: productId aus list_available_subscriptions. Laut API-Spec
        schaltet product_id = -1 das Abo ab.
    autorenew: automatische Verlängerung (optional, wird nur gesendet, wenn gesetzt).
    Schreibende Aktion: erfordert confirm=true.
    """
    iccid = _validated(v.validate_iccid, iccid)
    _validated(v.validate_choice, type, v.SUBSCRIPTION_TYPES, "type")
    body: dict[str, Any] = {"type": type, "product_id": product_id}
    if autorenew is not None:
        body["autorenew"] = autorenew
    action = (
        f"Abo der SIM {iccid} abschalten ({_payload_text(body)})"
        if product_id == -1
        else f"Abo der SIM {iccid} ändern ({_payload_text(body)}) - kann Kosten auslösen"
    )
    _require_confirm(confirm, action)
    return await _call(lambda c: c.modify_subscription(iccid, body))


@mcp.tool()
async def activate_sim(
    iccid: str,
    sim_alias: str,
    autorenew: bool | None = None,
    confirm: bool = False,
) -> Any:
    """Physische SIM-Karte aktivieren/registrieren (POST /api/v1/sims/register).

    ACHTUNG: löst ein Abo und damit Kosten aus.
    sim_alias: Alias, maximal 24 Zeichen (laut Spec Pflichtfeld).
    autorenew: automatische Verlängerung (optional, wird nur gesendet, wenn gesetzt).
    Schreibende Aktion: erfordert confirm=true.
    """
    iccid = _validated(v.validate_iccid, iccid)
    sim_alias = _validated(v.validate_alias, sim_alias, v.SIM_ALIAS_MAX)
    body: dict[str, Any] = {"iccid": iccid, "sim_alias": sim_alias}
    if autorenew is not None:
        body["autorenew"] = autorenew
    _require_confirm(confirm, f"SIM aktivieren ({_payload_text(body)}) - löst Kosten aus")
    return await _call(lambda c: c.activate_sim(body))


@mcp.tool()
async def activate_esim(
    product_id: int,
    sim_alias: str,
    autorenew: bool | None = None,
    confirm: bool = False,
) -> Any:
    """Neue eSIM bestellen/aktivieren (POST /api/v1/sims/esim).

    ACHTUNG: löst ein Abo und damit Kosten aus.
    product_id: productId aus list_available_subscriptions (ohne iccid).
    sim_alias: Alias, maximal 12 Zeichen (laut Spec Pflichtfeld).
    autorenew: automatische Verlängerung (optional, wird nur gesendet, wenn gesetzt).
    Die Antwort enthält eine order_id; die eSIM-Details (z.B. matching_id)
    liefert danach get_sim_by_order.
    Schreibende Aktion: erfordert confirm=true.
    """
    sim_alias = _validated(v.validate_alias, sim_alias, v.ESIM_ALIAS_MAX)
    body: dict[str, Any] = {"product_id": product_id, "sim_alias": sim_alias}
    if autorenew is not None:
        body["autorenew"] = autorenew
    _require_confirm(confirm, f"eSIM aktivieren ({_payload_text(body)}) - löst Kosten aus")
    return await _call(lambda c: c.activate_esim(body))


@mcp.tool()
async def network_reset(iccid: str, confirm: bool = False) -> Any:
    """Netzwerk-Reset für eine SIM-Karte auslösen (POST /api/v1/sims/networkreset).

    Schreibende Aktion: erfordert confirm=true.
    """
    iccid = _validated(v.validate_iccid, iccid)
    _require_confirm(confirm, f"Netzwerk-Reset für SIM {iccid} auslösen")
    return await _call(lambda c: c.network_reset(iccid))


# ---------------------------------------------------------------------------
# HTTP-Transport (Cloud/Docker) mit Bearer-Auth
# ---------------------------------------------------------------------------


class _BearerAuthMiddleware:
    """Minimalistische ASGI-Middleware: prüft 'Authorization: Bearer <token>'."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode("latin-1")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"")
        # Konstantzeit-Vergleich, damit das Token nicht über Antwortzeiten erratbar ist.
        if not hmac.compare_digest(auth_header, self.expected):
            from starlette.responses import JSONResponse

            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _build_http_app(api_key: str, host: str = "0.0.0.0"):
    if not api_key:
        raise RuntimeError(
            "MCP_TRANSPORT=http erfordert MCP_API_KEY (statisches Bearer-Token) - "
            "aus Sicherheitsgründen kein Start ohne Token."
        )
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.middleware.cors import CORSMiddleware

    # transport_security: DNS-Rebinding-Schutz deaktiviert, analog zu
    # plesk-mcp/also-mcp - sonst blockt das SDK jeden Request mit einem
    # Host-Header, der nicht "localhost"/eine IP ist (421 "Invalid Host
    # header"), obwohl der Server bewusst unter einer echten Domain erreichbar
    # ist. Die eigentliche Absicherung übernimmt MCP_API_KEY/_BearerAuthMiddleware.
    # json_response=True: einfache application/json-Antworten statt SSE-
    # Stream - robuster hinter Reverse-Proxies.
    starlette_app = mcp.streamable_http_app(
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        host=host,
    )
    secured_app = _BearerAuthMiddleware(starlette_app, api_key)
    # CORS aussen um die Auth-Middleware: Browser-basierte MCP-Clients (z.B.
    # Claude.ai) rufen den Endpoint per Cross-Origin-Fetch auf. Preflight-
    # OPTIONS-Requests (ohne Authorization-Header) beantwortet CORSMiddleware
    # direkt, bevor sie die Bearer-Prüfung erreichen.
    return CORSMiddleware(
        secured_app,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )


async def _run_http_server(api_key: str, host: str, port: int) -> None:
    import uvicorn

    app = _build_http_app(api_key, host)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    srv = uvicorn.Server(config)
    logger.info("dr-mcp %s HTTP server running on %s:%s", __version__, host, port)
    await srv.serve()


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http"):
        api_key = os.environ.get("MCP_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "MCP_TRANSPORT=http erfordert MCP_API_KEY (statisches Bearer-Token) - "
                "aus Sicherheitsgründen kein Start ohne Token."
            )
        import asyncio

        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "8000"))
        asyncio.run(_run_http_server(api_key, host, port))
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

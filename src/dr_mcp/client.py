"""
Async HTTP-Client für die Digital Republic API (SIM-/eSIM-Verwaltung).

Referenz: OpenAPI-Spec des Anbieters unter <DR_BASE_URL>/api/api-docs.

Authentifizierung (OAuth2 Client Credentials):
  - Token-URL laut Spec: <DR_BASE_URL>/oauth/token
  - Client-ID und -Secret werden als Formular-Parameter im Body übergeben
    (grant_type=client_credentials&client_id=...&client_secret=...).
    Die Spec sagt dazu nichts. Geprüft wurde es mit einem Request mit
    ungültigen Dummy-Daten: Body-Credentials werden vom Token-Endpunkt
    ausgewertet (Antwort 401 mit WWW-Authenticate: Form realm="oauth2/client",
    error="invalid_client" als JSON). HTTP Basic mit ungültigen Daten liefert
    dagegen nur einen 302-Redirect auf eine Fehlerseite ohne verwertbare
    Fehlermeldung.
  - Das Access-Token wird bis kurz vor Ablauf (expires_in) gecacht. Antwortet
    die API mit 401, wird das Token einmal neu geholt und der Request
    einmal wiederholt.

Sicherheit:
  - Client-Secret und Access-Token werden nie geloggt und nie in
    Fehlermeldungen weitergegeben. Die API gibt bei ungültigem Token das
    Token selbst im error_description zurück ("Invalid access token: <token>"),
    deshalb werden Fehlertexte vor der Weitergabe bereinigt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("dr_mcp.client")

# Sicherheitsabstand in Sekunden: Token wird so viel früher als nötig erneuert.
_TOKEN_EXPIRY_MARGIN = 30
# Fallback, falls die Token-Antwort kein expires_in enthält: nicht cachen
# über diese Dauer hinaus (401-Retry fängt ein vorzeitig abgelaufenes Token ab).
_TOKEN_DEFAULT_LIFETIME = 300
# Maximale Länge eines weitergereichten Fehlertexts aus der API.
_MAX_ERROR_TEXT = 500


class DigitalRepublicError(RuntimeError):
    """Fehler der Digital Republic API oder beim Zugriff darauf.

    Die Meldung ist bereinigt (keine Secrets/Tokens) und kann direkt an den
    MCP-Client weitergegeben werden.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _format_simple_response_errors(body: dict[str, Any]) -> str:
    """Fehlertexte aus einer SimpleResponse zusammensetzen (message, errorMessages, errors)."""
    parts: list[str] = []
    message = body.get("message")
    if isinstance(message, str) and message.strip():
        parts.append(message.strip())
    error_messages = body.get("errorMessages")
    if isinstance(error_messages, list):
        parts.extend(str(m).strip() for m in error_messages if str(m).strip())
    errors = body.get("errors")
    if isinstance(errors, list):
        for err in errors:
            if isinstance(err, dict):
                code = err.get("code")
                msg = str(err.get("message") or "").strip()
                if code is not None and msg:
                    parts.append(f"[{code}] {msg}")
                elif msg:
                    parts.append(msg)
                elif code is not None:
                    parts.append(f"[{code}]")
    # Duplikate entfernen, Reihenfolge behalten
    seen: set[str] = set()
    unique = [p for p in parts if not (p in seen or seen.add(p))]
    return "; ".join(unique)


class DigitalRepublicClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        *,
        token_url: str | None = None,
        scope: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token_url = token_url or f"{self.base_url}/oauth/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope or None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        # follow_redirects=False: der Token-Endpunkt antwortet bei manchen
        # Fehlern mit 302 auf eine HTML-Fehlerseite - das soll als Fehler
        # erkannt und nicht als "Erfolg" verfolgt werden.
        self._http = httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- Bereinigung --------------------------------------------------------

    def _redact(self, text: str) -> str:
        """Secret und aktuelles Token aus einem Text entfernen, Länge begrenzen."""
        for secret in (self._client_secret, self._token):
            if secret:
                text = text.replace(secret, "***")
        text = text.strip()
        if len(text) > _MAX_ERROR_TEXT:
            text = text[:_MAX_ERROR_TEXT] + " ..."
        return text

    def _error_text_from_response(self, resp: httpx.Response) -> str:
        """Lesbaren Fehlertext aus einer API-Antwort holen (JSON bevorzugt)."""
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            simple = _format_simple_response_errors(body)
            if simple:
                return self._redact(simple)
            desc = body.get("error_description") or body.get("error")
            if desc:
                return self._redact(str(desc))
        ctype = resp.headers.get("content-type", "")
        if resp.text and "html" not in ctype:
            return self._redact(resp.text)
        return ""

    # -- Token ----------------------------------------------------------------

    def _token_valid(self) -> bool:
        return bool(self._token) and time.monotonic() < self._token_expires_at

    async def _fetch_token(self) -> str:
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scope:
            data["scope"] = self._scope
        try:
            resp = await self._http.post(self.token_url, data=data, headers={"Accept": "application/json"})
        except httpx.TimeoutException:
            raise DigitalRepublicError("Token-Anfrage: Zeitüberschreitung beim Verbindungsaufbau zur API") from None
        except httpx.HTTPError as exc:
            raise DigitalRepublicError(f"Token-Anfrage: Verbindungsfehler ({type(exc).__name__})") from None

        if resp.status_code != 200:
            detail = self._error_text_from_response(resp)
            logger.warning("Token-Anfrage fehlgeschlagen: HTTP %s", resp.status_code)
            msg = f"Token-Anfrage fehlgeschlagen (HTTP {resp.status_code})"
            if detail:
                msg += f": {detail}"
            elif 300 <= resp.status_code < 400:
                msg += ": unerwartete Weiterleitung - Client-ID/Secret und DR_BASE_URL prüfen"
            raise DigitalRepublicError(msg, status_code=resp.status_code)

        try:
            body = resp.json()
        except ValueError:
            raise DigitalRepublicError("Token-Anfrage: Antwort ist kein JSON") from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token or not isinstance(token, str):
            raise DigitalRepublicError("Token-Anfrage: Antwort enthält kein access_token")

        try:
            lifetime = float(body.get("expires_in", _TOKEN_DEFAULT_LIFETIME))
        except (TypeError, ValueError):
            lifetime = _TOKEN_DEFAULT_LIFETIME
        self._token = token
        self._token_expires_at = time.monotonic() + max(lifetime - _TOKEN_EXPIRY_MARGIN, 0)
        logger.info("Neues Access-Token geholt (gültig ca. %d s)", int(lifetime))
        return token

    async def _get_token(self, force_refresh: bool = False) -> str:
        async with self._token_lock:
            if force_refresh or not self._token_valid():
                self._token = None
                return await self._fetch_token()
            assert self._token is not None
            return self._token

    # -- Requests -----------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Request an die API senden, JSON-Antwort zurückgeben.

        Wirft DigitalRepublicError bei HTTP-Fehlern, Verbindungsproblemen und
        bei einer SimpleResponse mit status NOK.
        """
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}

        resp = await self._send(method, url, params, json, await self._get_token())
        if resp.status_code == 401:
            logger.info("%s %s -> 401, hole neues Token und wiederhole einmal", method, path)
            resp = await self._send(method, url, params, json, await self._get_token(force_refresh=True))

        logger.info("%s %s -> HTTP %s", method, path, resp.status_code)

        if resp.status_code >= 300:
            detail = self._error_text_from_response(resp)
            msg = f"API-Fehler (HTTP {resp.status_code})"
            if resp.status_code == 404 and not detail:
                detail = "SIM/Auftrag für diesen Kunden nicht gefunden"
            if detail:
                msg += f": {detail}"
            raise DigitalRepublicError(msg, status_code=resp.status_code)

        if not resp.content:
            return None
        try:
            body = resp.json()
        except ValueError:
            raise DigitalRepublicError(
                f"API-Antwort ist kein JSON (HTTP {resp.status_code})", status_code=resp.status_code
            ) from None

        if isinstance(body, dict) and body.get("status") == "NOK":
            detail = _format_simple_response_errors(body)
            msg = "API meldet NOK"
            if detail:
                msg += f": {self._redact(detail)}"
            raise DigitalRepublicError(msg, status_code=resp.status_code)

        return body

    async def _send(
        self,
        method: str,
        url: str,
        params: dict[str, Any],
        json: dict[str, Any] | None,
        token: str,
    ) -> httpx.Response:
        try:
            return await self._http.request(
                method,
                url,
                params=params or None,
                json=json,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.TimeoutException:
            raise DigitalRepublicError("Zeitüberschreitung bei der Anfrage an die API") from None
        except httpx.HTTPError as exc:
            raise DigitalRepublicError(f"Verbindungsfehler zur API ({type(exc).__name__})") from None

    # -- Endpunkte ----------------------------------------------------------

    async def list_sims(self, **params: Any) -> Any:
        return await self.request("GET", "/api/v1/sims", params=params)

    async def get_sim(self, iccid: str, history: bool | None = None) -> Any:
        params = {"history": str(history).lower()} if history is not None else None
        return await self.request("GET", f"/api/v1/sims/{quote(iccid, safe='')}", params=params)

    async def rename_sim(self, iccid: str, sim_alias: str) -> Any:
        return await self.request("PUT", f"/api/v1/sims/{quote(iccid, safe='')}", json={"sim_alias": sim_alias})

    async def get_sim_by_order(self, order_id: str) -> Any:
        return await self.request("GET", f"/api/v1/sims/orders/{quote(order_id, safe='')}")

    async def list_available_subscriptions(self, iccid: str | None = None) -> Any:
        if iccid:
            return await self.request("GET", f"/api/v1/sims/{quote(iccid, safe='')}/available_subscriptions")
        return await self.request("GET", "/api/v1/sims/available_subscriptions")

    async def modify_subscription(self, iccid: str, body: dict[str, Any]) -> Any:
        return await self.request("PUT", f"/api/v1/sims/{quote(iccid, safe='')}/subscriptions", json=body)

    async def activate_sim(self, body: dict[str, Any]) -> Any:
        return await self.request("POST", "/api/v1/sims/register", json=body)

    async def activate_esim(self, body: dict[str, Any]) -> Any:
        return await self.request("POST", "/api/v1/sims/esim", json=body)

    async def network_reset(self, iccid: str) -> Any:
        return await self.request("POST", "/api/v1/sims/networkreset", json={"iccid": iccid})

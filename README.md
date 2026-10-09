# dr-mcp

MCP-Server für die Digital Republic API (Schweizer Mobilfunk-Anbieter, SIM-/eSIM-Verwaltung für Geschäftskunden). Damit lassen sich SIM-Karten auflisten, Details und Abos abfragen, Aliase ändern, Abos wechseln, SIMs/eSIMs aktivieren und Netzwerk-Resets auslösen.

Zwei Betriebsarten:

- **stdio (lokal)**: klassischer lokaler MCP-Server via uvx/Claude Desktop.
- **HTTP (Cloud)**: Docker-Container mit Streamable-HTTP-Transport (Portainer), geschützt durch ein statisches Bearer-Token.

Alle Zugangsdaten und Einstellungen kommen ausschliesslich aus Umgebungsvariablen. Im Repo stehen keine Secrets, Hostnamen oder Kundendaten.

## Authentifizierung gegenüber der API

- OAuth2 Client Credentials, Token-URL laut OpenAPI-Spec: `<DR_BASE_URL>/oauth/token`, Scopes `read` und `write`.
- Client-ID und Client-Secret werden als Formular-Parameter im Body gesendet (`grant_type=client_credentials&client_id=...&client_secret=...`). Die Spec sagt dazu nichts. Ein Test-Request mit ungültigen Dummy-Daten hat gezeigt, dass der Token-Endpunkt Body-Credentials auswertet (saubere OAuth-Fehlerantwort `invalid_client`), während HTTP Basic mit ungültigen Daten nur auf eine Fehlerseite umleitet. Mit echten Zugangsdaten ist das noch einmal zu bestätigen (erster Aufruf von `list_sims`).
- Das Access-Token wird bis kurz vor Ablauf (`expires_in`) gecacht. Bei `401` wird es einmal neu geholt und der Request einmal wiederholt.
- `scope` wird nur mitgeschickt, wenn `DR_SCOPE` gesetzt ist.

## Umgebungsvariablen

| Variable | Pflicht | Standard | Beschreibung |
|---|---|---|---|
| `DR_BASE_URL` | ja | – | Basis-URL der API (servers-Eintrag der OpenAPI-Spec des Anbieters), z.B. `https://api.example.com/dr` |
| `DR_CLIENT_ID` | ja | – | OAuth2-Client-ID |
| `DR_CLIENT_SECRET` | ja | – | OAuth2-Client-Secret |
| `DR_TOKEN_URL` | nein | `<DR_BASE_URL>/oauth/token` | Token-Endpunkt, falls abweichend |
| `DR_SCOPE` | nein | – | z.B. `read write`; ohne Wert wird kein `scope` gesendet |
| `DR_TIMEOUT` | nein | `30` | HTTP-Timeout in Sekunden |
| `LOG_LEVEL` | nein | `INFO` | Log-Level (Logs gehen nach stderr, nie mit Secrets/Tokens) |
| `MCP_TRANSPORT` | nein | `stdio` | `stdio` = lokal, `http` = Cloud-Modus |
| `MCP_API_KEY` | **ja, im HTTP-Modus** | – | Statisches Bearer-Token für den MCP-Endpunkt. Ohne dieses Token startet der HTTP-Modus nicht. Erzeugen mit `openssl rand -hex 32` |
| `MCP_HOST` | nein | `0.0.0.0` | Bind-Adresse im Container |
| `MCP_PORT` | nein | `8000` | Port im Container |
| `MCP_HOST_PORT` | nein | `8425` | Port auf dem Docker-Host (nur docker-compose) |

## Tools

Lesend:

| Tool | API | Beschreibung |
|---|---|---|
| `get_version` | – | Version und Commit des laufenden Servers |
| `list_sims` | `GET /api/v1/sims` | SIMs auflisten; optional `search`, `status`, `page`, `page_size`, `sort_field`, `sort_direction` |
| `get_sim` | `GET /api/v1/sims/{iccid}` | Details einer SIM, optional mit `history=true` |
| `get_sim_by_order` | `GET /api/v1/sims/orders/{order_id}` | SIM zu einer Auftragsnummer |
| `list_available_subscriptions` | `GET /api/v1/sims/{iccid}/available_subscriptions` bzw. `GET /api/v1/sims/available_subscriptions` | Buchbare Abos; ohne `iccid` die eSIM-Variante |

Schreibend (alle verlangen `confirm=true`, sonst wird nichts gesendet):

| Tool | API | Beschreibung |
|---|---|---|
| `rename_sim` | `PUT /api/v1/sims/{iccid}` | Alias ändern (max. 24 Zeichen) |
| `modify_subscription` | `PUT /api/v1/sims/{iccid}/subscriptions` | Abo ändern: `type` (`Data_CH`/`Data_Roaming`), `product_id`, optional `autorenew`. Laut Spec schaltet `product_id=-1` das Abo ab. **Kann Kosten auslösen.** |
| `activate_sim` | `POST /api/v1/sims/register` | SIM aktivieren: `iccid`, `sim_alias` (max. 24), optional `autorenew`. **Löst Kosten aus.** |
| `activate_esim` | `POST /api/v1/sims/esim` | eSIM aktivieren: `product_id`, `sim_alias` (max. 12), optional `autorenew`. **Löst Kosten aus.** |
| `network_reset` | `POST /api/v1/sims/networkreset` | Netzwerk-Reset für eine SIM |

Eingaben werden vor dem Request geprüft: ICCID nur Ziffern, Längen der Aliase, erlaubte Werte für `type`, `status`, `sort_field`, `sort_direction`, `page`/`page_size` ≥ 1.

Antwortet die API mit `status: "NOK"` (auch bei HTTP 200), wird das als Fehler an den Client gemeldet, inklusive `message`, `errorMessages` und `errors` aus der Antwort. HTTP-Fehler werden mit Status und dem Fehlertext der API gemeldet; Stacktraces gehen nie nach aussen.

Typischer eSIM-Ablauf:

1. `list_available_subscriptions` (ohne `iccid`) – `productId` wählen
2. `activate_esim` mit `confirm=true` – liefert eine `order_id`
3. `get_sim_by_order` – eSIM-Details (z.B. `matching_id`)

## Lokaler Betrieb (stdio, uvx)

```json
{
  "mcpServers": {
    "digitalrepublic": {
      "command": "uvx",
      "args": ["--from", "C:/Pfad/zu/dr-mcp", "--python", "3.12", "dr-mcp"],
      "env": {
        "DR_BASE_URL": "https://api.example.com/dr",
        "DR_CLIENT_ID": "your-client-id",
        "DR_CLIENT_SECRET": "your-client-secret"
      }
    }
  }
}
```

**Wichtig:** uvx cached gebaute Pakete pro Paketversion. Die Paketversion in `pyproject.toml` ändert sich nur bei Major/Minor, der automatisch gezählte Patch-Teil (siehe "Versionierung") nicht. Nach einer Änderung deshalb `uv cache clean` ausführen (oder einmal `uvx --refresh ...`) und Claude Desktop neu starten. `get_version` zeigt danach den neuen Stand.

## Tests

```bash
pip install -e ".[test]"
pytest
```

Die Tests laufen komplett gegen eine gemockte API (respx), es gibt keinen Zugriff auf die echte API.

## Docker-Image (GitHub Actions → ghcr.io)

Bei jedem Push auf `main` laufen zuerst die Tests, danach baut `.github/workflows/docker-publish.yml` das Image und veröffentlicht es als `ghcr.io/<owner>/<repo>` mit den Tags `latest`, `<commit-sha>` und `<version>`.

**Versionierung:** Die Version ist `<Major.Minor>.<Patch>`. Major.Minor steht in `pyproject.toml` und wird nur von Hand geändert. Der Patch-Teil ist die Anzahl Commits, die `src/`, `pyproject.toml`, das `Dockerfile` oder den Workflow geändert haben, und steigt damit bei jeder Code-Änderung automatisch. GitHub Actions gibt Version und Commit ins Image mit, `get_version` liefert beides.

Nach dem ersten Push ist das Package auf GitHub standardmässig privat. Entweder in den Package-Settings auf **Public** stellen oder in Portainer unter **Registries** einen ghcr.io-Eintrag mit einem Token (Scope `read:packages`) hinterlegen.

## Deployment via Portainer

1. **Stacks → Add stack**, Build method **Repository**, Repo-URL, Branch `main`, Compose-Pfad `docker-compose.yml`
2. Unter **Environment variables** setzen (landen nicht im Repo):
   - `DR_BASE_URL`, `DR_CLIENT_ID`, `DR_CLIENT_SECRET`
   - `MCP_API_KEY`
   - optional `MCP_HOST_PORT`, `DR_SCOPE`, `DR_TIMEOUT`
3. **Deploy the stack**
4. Updates: im Stack **Pull and redeploy**, danach mit `get_version` prüfen, welcher Stand läuft

Beispiel für die Stack-Variablen (nur Platzhalter):

```env
DR_BASE_URL=https://api.example.com/dr
DR_CLIENT_ID=your-client-id
DR_CLIENT_SECRET=your-client-secret
MCP_API_KEY=<openssl rand -hex 32>
MCP_HOST_PORT=8425
```

Vor den Container gehört ein Reverse-Proxy mit TLS (z.B. nginx), der eine eigene Subdomain auf `<docker-host>:<MCP_HOST_PORT>` weiterleitet. In Claude wird der Server dann als Remote-MCP mit `https://dr-mcp.example.com/mcp` und `MCP_API_KEY` als Bearer-Token eingebunden.

## Sicherheit

- Der HTTP-Modus startet nur mit gesetztem `MCP_API_KEY`, es gibt nie einen ungeschützten Endpunkt.
- Jeder Request braucht `Authorization: Bearer <MCP_API_KEY>`, sonst `401 unauthorized`. Der Vergleich läuft in konstanter Zeit.
- CORS-Preflight (`OPTIONS`) wird ohne Token beantwortet, damit browserbasierte MCP-Clients funktionieren; alle anderen Requests laufen durch die Token-Prüfung.
- Der DNS-Rebinding-Schutz des SDK ist bewusst deaktiviert (wie bei den anderen KDT-MCP-Servern), weil der Server unter einer echten Domain hinter einem Reverse-Proxy läuft. Die Zugriffskontrolle macht das Bearer-Token.
- Client-Secret und Access-Token werden nie geloggt und aus weitergereichten Fehlertexten entfernt (die API gibt bei ungültigem Token das Token selbst im Fehlertext zurück).
- `confirm=true` ist eine Schutzschiene gegen versehentliche Aufrufe, keine Zugriffskontrolle.
- Der Container läuft als unprivilegierter Benutzer.

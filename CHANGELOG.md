# Changelog

## 0.2

- Port-Mapping mit konfigurierbarer Bind-Adresse: `${MCP_BIND_ADDR:-127.0.0.1}:${MCP_HOST_PORT:-8425}:8000`. Bisher lauschte der Dienst auf allen Interfaces inkl. IPv6. **Vor dem ersten Redeploy `MCP_BIND_ADDR` im Stack auf die Host-IP setzen, die der Reverse-Proxy anspricht**, sonst ist der Dienst nur noch auf `127.0.0.1` erreichbar.
- Neuer Endpunkt `GET /healthz` ohne Auth, liefert nur `{"status": "ok"}`. Prüft nur, ob der Prozess antwortet, nicht die Digital-Republic-API. Alle anderen Pfade brauchen weiterhin das Bearer-Token.
- `HEALTHCHECK` im Dockerfile (Python/urllib auf `/healthz`, interval 30s, timeout 5s, start_period 15s, retries 3).

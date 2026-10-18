FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# Nicht als root laufen
RUN useradd --system --no-create-home --uid 10001 mcp
USER mcp

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    PYTHONUNBUFFERED=1

# Git-Commit und Version des Builds (von GitHub Actions gesetzt) - get_version
# gibt beides zurück, damit nach einem Redeploy prüfbar ist, welcher Stand wirklich läuft.
ARG GIT_SHA=unbekannt
ARG APP_VERSION=
ENV GIT_SHA=$GIT_SHA \
    APP_VERSION=$APP_VERSION

EXPOSE 8000

# /healthz ist ohne Auth erreichbar und prüft nur, ob der Prozess antwortet.
# Kein curl/wget im Image, darum per Python/urllib.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('MCP_PORT', '8000') + '/healthz', timeout=4)"]

CMD ["dr-mcp"]

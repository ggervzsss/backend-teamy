## Teamy API

FastAPI backend for Teamy authentication. The Docker stack uses MySQL 8.4 for local development and exposes it on host port `3307` by default.

For TiDB Cloud or another MySQL provider that requires TLS, set `DATABASE_SSL=true` in the deployment environment. You can also append `ssl=true` to `DATABASE_URL`; the app converts that into the SSL context needed by both Alembic and the async API runtime. If your provider asks for a CA file, set `DATABASE_SSL_CA=/etc/ssl/certs/ca-certificates.crt`.

### Local Docker startup

From the workspace root:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The API runs at `http://localhost:8000` and applies Alembic migrations on container startup.

### Local commands

```powershell
uv sync
uv run pytest
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

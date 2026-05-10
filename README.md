## Teamy API

FastAPI backend for Teamy authentication.

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

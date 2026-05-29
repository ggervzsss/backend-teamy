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

### Render deployment

Deploy the backend as a Docker web service. Leave the Render Docker Command blank so Render uses the Dockerfile `CMD`, or set it explicitly to:

```sh
./entrypoint.sh
```

The first runtime log line should be:

```text
Teamy entrypoint reached; running Docker image startup command.
```

If that line does not appear after `Deploying...`, the Render service is running a dashboard-level Docker Command instead of the image startup command.

Required production environment variables:

```text
DATABASE_URL=mysql+aiomysql://USER:PASSWORD@HOST:3306/teamy?charset=utf8mb4
DATABASE_SSL=true
SECRET_KEY=<long random value>
FRONTEND_URL=https://your-frontend-domain
CORS_ORIGINS_RAW=https://your-frontend-domain
COOKIE_SECURE=true
COOKIE_SAMESITE=none
```

Do not use a Render PostgreSQL database URL for `DATABASE_URL`; this backend is configured for MySQL-compatible databases such as MySQL or TiDB Cloud. Render provides `PORT` automatically and the entrypoint binds Uvicorn to that value.

### Email notifications

Set `RESEND_API_KEY` and `RESEND_FROM_EMAIL` in the backend environment to enable email notifications. `RESEND_FROM_EMAIL` should use a sender/domain verified in Resend for production.

Local email signup also uses Resend. When `SIGNUP_EMAIL_VERIFICATION_REQUIRED=true`, `/auth/signup/verification` returns `503` until `RESEND_API_KEY` is configured. Resend's `onboarding@resend.dev` sender is only for testing to your own Resend account email; use a verified domain sender such as `Teamy <noreply@your-verified-domain.com>` for other recipients.

Task and announcement event emails are queued automatically by the API. Due-date reminders are queued by calling:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/notifications/reminders/due `
  -Headers @{ "X-Teamy-Reminder-Secret" = $env:NOTIFICATION_REMINDER_SECRET }
```

Run that endpoint daily from your scheduler. It sends task reminders to assigned users and announcement reminders to project members for items scheduled today or tomorrow.

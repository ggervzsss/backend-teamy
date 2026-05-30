from __future__ import annotations

from sqlalchemy.engine import make_url

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    url = make_url(settings.database_url)
    print(
        "Startup configuration: "
        f"database_driver={url.drivername}, "
        f"database_host={url.host or 'local'}, "
        f"database_port={url.port or 'default'}, "
        f"database_name={url.database or 'missing'}, "
        f"database_ssl={settings.database_ssl_enabled}, "
        f"cors_origins={','.join(settings.cors_origins)}"
    )


if __name__ == "__main__":
    main()

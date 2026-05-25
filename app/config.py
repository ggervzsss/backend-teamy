from functools import lru_cache
import ssl

from pydantic import AnyHttpUrl, Field
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

RESERVED_DATABASE_NAMES = {"information_schema", "mysql", "performance_schema", "sys"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "mysql+aiomysql://teamy:teamy@localhost:3307/teamy?charset=utf8mb4"
    secret_key: str = Field(default="change-me-in-development-only", min_length=16)
    session_cookie_name: str = "teamy_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 7
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    frontend_url: AnyHttpUrl = "http://localhost:5173"
    cors_origins_raw: str = "http://localhost:5173,http://127.0.0.1:5173"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    cloudinary_secure: bool = True
    resend_api_key: str = ""
    resend_from_email: str = "Teamy <onboarding@resend.dev>"
    signup_email_verification_required: bool = True
    signup_verification_code_ttl_seconds: int = 10 * 60
    signup_verification_max_attempts: int = 5
    signup_verification_code_length: int = 6
    notification_reminder_secret: str = ""
    notification_reminder_timezone: str = "Asia/Manila"
    database_ssl: bool = False
    database_ssl_verify: bool = True
    database_ssl_ca: str | None = None

    @model_validator(mode="after")
    def validate_database_name(self) -> "Settings":
        database_name = make_url(self.database_url).database
        if database_name is None:
            raise ValueError("DATABASE_URL must include an application database name, for example /teamy")
        if database_name.lower() in RESERVED_DATABASE_NAMES:
            raise ValueError(
                f"DATABASE_URL points to the MySQL/TiDB system database '{database_name}'. "
                "Use an application database such as 'teamy' instead."
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    def _database_url_for_driver(self, drivername: str) -> URL:
        url = make_url(self.database_url)
        query = dict(url.query)
        for key in ("ssl", "sslmode", "ssl_mode", "ssl-mode", "ssl_ca", "ssl_verify_cert", "ssl_verify_identity"):
            query.pop(key, None)
        return url.set(drivername=drivername, query=query)

    @property
    def database_ssl_enabled(self) -> bool:
        url = make_url(self.database_url)
        query = url.query
        ssl_mode = str(query.get("ssl") or query.get("sslmode") or query.get("ssl_mode") or query.get("ssl-mode") or "").lower()
        return self.database_ssl or ssl_mode in {"1", "true", "yes", "require", "required", "verify-ca", "verify_identity", "verify-identity"}

    @property
    def database_connect_args(self) -> dict:
        if not self.database_ssl_enabled:
            return {}

        query = make_url(self.database_url).query
        ca_file = query.get("ssl_ca") or self.database_ssl_ca
        verify_cert = str(query.get("ssl_verify_cert", self.database_ssl_verify)).lower() not in {"0", "false", "no"}
        context = ssl.create_default_context(cafile=ca_file)
        if not verify_cert:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        elif str(query.get("ssl_verify_identity", "true")).lower() in {"0", "false", "no"}:
            context.check_hostname = False
        return {"ssl": context}

    @property
    def async_database_url(self) -> str:
        url = make_url(self.database_url)
        async_drivers = {
            "mysql": "mysql+aiomysql",
            "mysql+mysqldb": "mysql+aiomysql",
            "mysql+pymysql": "mysql+aiomysql",
        }
        return self._database_url_for_driver(async_drivers.get(url.drivername, url.drivername)).render_as_string(
            hide_password=False
        )

    @property
    def sync_database_url(self) -> str:
        url = make_url(self.database_url)
        sync_drivers = {
            "mysql": "mysql+pymysql",
            "mysql+mysqldb": "mysql+pymysql",
            "mysql+aiomysql": "mysql+pymysql",
            "mysql+asyncmy": "mysql+pymysql",
        }
        return self._database_url_for_driver(sync_drivers.get(url.drivername, url.drivername)).render_as_string(
            hide_password=False
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

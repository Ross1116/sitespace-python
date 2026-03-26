import logging
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    # Database - Use PostgreSQL for production, SQLite for testing
    database_url: str = Field("sqlite:///./test.db", validation_alias="DATABASE_URL")

    # JWT
    jwt_secret: str = Field("", validation_alias="JWT_SECRET")
    jwt_algorithm: str = Field("HS512", validation_alias="JWT_ALGORITHM")
    jwt_expiration_ms: int = Field(86400000, validation_alias="JWT_EXPIRATION_MS")

    # Token expiration settings
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(30, validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(7, validation_alias="REFRESH_TOKEN_EXPIRE_DAYS")
    EMAIL_VERIFICATION_EXPIRE_HOURS: int = Field(24, validation_alias="EMAIL_VERIFICATION_EXPIRE_HOURS")
    PASSWORD_RESET_EXPIRE_HOURS: int = Field(1, validation_alias="PASSWORD_RESET_EXPIRE_HOURS")

    # App
    secret_key: str = Field("", validation_alias="SECRET_KEY")
    export_files_absolute_path: str = Field("/app/uploads/", validation_alias="EXPORT_FILES_ABSOLUTE_PATH")
    export_files_server_path: str = Field("getFile", validation_alias="EXPORT_FILES_SERVER_PATH")

    # Server
    host: str = Field("0.0.0.0", validation_alias="HOST")
    port: int = Field(8080, validation_alias="PORT")
    debug: bool = Field(False, validation_alias="DEBUG")

    # CORS - production origins only; dev origins included when DEBUG=True
    cors_origins: str | list[str] = Field(
        default_factory=lambda: [
            "https://sitespace.vercel.app",
            "https://sitespace.com.au",
            "https://www.sitespace.com.au",
        ],
        validation_alias="CORS_ORIGINS",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def effective_cors_origins(self) -> list[str]:
        origins = [origin.strip() for origin in self.cors_origins if origin.strip()]
        if self.debug:
            origins += ["http://localhost:3000", "http://localhost:5173"]
        return origins

    # Email / Mailtrap
    MAILTRAP_USE_SANDBOX: bool = Field(True, validation_alias="MAILTRAP_USE_SANDBOX")
    MAILTRAP_TOKEN: Optional[str] = Field(None, validation_alias="MAILTRAP_TOKEN")
    MAILTRAP_INBOX_ID: Optional[str] = Field(None, validation_alias="MAILTRAP_INBOX_ID")

    # Email Sender Info
    FROM_EMAIL: str = Field("noreply@sitespace.com", validation_alias="FROM_EMAIL")
    FROM_NAME: str = Field("Sitespace Team", validation_alias="FROM_NAME")

    # Frontend URL for email links / cookie origin (set in env for prod)
    FRONTEND_URL: str = Field("http://localhost:3000", validation_alias="FRONTEND_URL")

    # Optional cookie domain (set in production if needed). Example: ".example.com"
    COOKIE_DOMAIN: Optional[str] = Field(None, validation_alias="COOKIE_DOMAIN")

    # Is production flag
    IS_PRODUCTION: bool = Field(False, validation_alias="IS_PRODUCTION")

    # App settings
    APP_NAME: str = Field("Sitespace", validation_alias="APP_NAME")

    # AI provider and model selection
    AI_PROVIDER: str = Field("anthropic", validation_alias="AI_PROVIDER")
    AI_API_KEY: Optional[str] = Field(None, validation_alias="AI_API_KEY")
    AI_MODEL: str = Field("claude-haiku-4-5-20251001", validation_alias="AI_MODEL")
    AI_ENABLED: bool = Field(True, validation_alias="AI_ENABLED")

    # AI timeout and budget defaults
    AI_TIMEOUT_STRUCTURE: int = Field(20, validation_alias="AI_TIMEOUT_STRUCTURE")
    AI_TIMEOUT_CLASSIFY: int = Field(30, validation_alias="AI_TIMEOUT_CLASSIFY")
    AI_TIMEOUT_WORK_PROFILE: int = Field(25, validation_alias="AI_TIMEOUT_WORK_PROFILE")
    AI_UPLOAD_COST_BUDGET_USD: float = Field(5.0, validation_alias="AI_UPLOAD_COST_BUDGET_USD")

    # Nightly lookahead scheduler defaults
    NIGHTLY_LOOKAHEAD_HOUR: int = Field(18, validation_alias="NIGHTLY_LOOKAHEAD_HOUR")
    NIGHTLY_LOOKAHEAD_MINUTE: int = Field(30, validation_alias="NIGHTLY_LOOKAHEAD_MINUTE")
    NIGHTLY_LOOKAHEAD_TIMEZONE: str = Field("Australia/Adelaide", validation_alias="NIGHTLY_LOOKAHEAD_TIMEZONE")


settings = Settings()

logger = logging.getLogger(__name__)

# Secrets must be set for any non-debug run. Allow empty only in DEBUG to
# avoid accidental insecure staging/prod deployments.
if not settings.jwt_secret or not settings.secret_key:
    if settings.debug:
        logger.warning(
            "Insecure configuration: JWT_SECRET/SECRET_KEY are empty while DEBUG=True. "
            "Do not use this configuration in staging/production."
        )
    else:
        raise ValueError(
            "Configuration error: JWT_SECRET and SECRET_KEY must be set "
            "(empty secrets are only allowed when DEBUG=True)."
        )

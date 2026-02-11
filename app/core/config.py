from pydantic_settings import BaseSettings
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    # Database - Use PostgreSQL for production, SQLite for testing
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./test.db")
    
    # JWT
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS512")
    jwt_expiration_ms: int = int(os.getenv("JWT_EXPIRATION_MS", "86400000"))
    
    # Token expiration settings
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    EMAIL_VERIFICATION_EXPIRE_HOURS: int = 24
    PASSWORD_RESET_EXPIRE_HOURS: int = 1
    
    # App
    secret_key: str = os.getenv("SECRET_KEY", "")
    export_files_absolute_path: str = os.getenv("EXPORT_FILES_ABSOLUTE_PATH", "/app/uploads/")
    export_files_server_path: str = os.getenv("EXPORT_FILES_SERVER_PATH", "getFile")
    
    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8080"))
    debug: bool = os.getenv("DEBUG", "False").strip().lower() in ("true", "1", "yes", "on")
    
    # CORS — production origins only; dev origins included when DEBUG=True
    cors_origins: list = os.getenv(
        "CORS_ORIGINS",
        "https://sitespace.vercel.app,https://sitespace.com.au,https://www.sitespace.com.au"
    ).split(",")

    @property
    def effective_cors_origins(self) -> list:
        origins = [o.strip() for o in self.cors_origins if o.strip()]
        if self.debug:
            origins += ["http://localhost:3000", "http://localhost:5173"]
        return origins
    
    # Email / Mailtrap
    MAILTRAP_USE_SANDBOX: bool = os.getenv("MAILTRAP_USE_SANDBOX", "True").strip().lower() in ("true", "1", "yes", "on")
    MAILTRAP_TOKEN: Optional[str] = os.getenv("MAILTRAP_TOKEN")
    MAILTRAP_INBOX_ID: Optional[str] = os.getenv("MAILTRAP_INBOX_ID")
    
    # Email Sender Info
    FROM_EMAIL: str = os.getenv("FROM_EMAIL", "noreply@sitespace.com")
    FROM_NAME: str = os.getenv("FROM_NAME", "Sitespace Team")
    
    # Frontend URL for email links / cookie origin (set in env for prod)
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
    
    # Optional cookie domain (set in production if needed). Example: ".example.com"
    COOKIE_DOMAIN: Optional[str] = os.getenv("COOKIE_DOMAIN", None)
    
    # Is production flag
    IS_PRODUCTION: bool = os.getenv("IS_PRODUCTION", "False").strip().lower() in ("true", "1", "yes", "on")
    
    # App settings
    APP_NAME: str = "Sitespace"

    class Config:
        env_file = ".env"

settings = Settings()

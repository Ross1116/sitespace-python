from pydantic_settings import BaseSettings
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    # Database - Use PostgreSQL for production, SQLite for testing
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./test.db")
    
    # JWT
    jwt_secret: str = os.getenv("JWT_SECRET", "Paragon$123")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS512")
    jwt_expiration_ms: int = int(os.getenv("JWT_EXPIRATION_MS", "86400000"))
    
    # Token expiration settings
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    EMAIL_VERIFICATION_EXPIRE_HOURS: int = 24
    PASSWORD_RESET_EXPIRE_HOURS: int = 1
    
    # App
    secret_key: str = os.getenv("SECRET_KEY", "PARAGON$87654321")
    export_files_absolute_path: str = os.getenv("EXPORT_FILES_ABSOLUTE_PATH", "/app/uploads/")
    export_files_server_path: str = os.getenv("EXPORT_FILES_SERVER_PATH", "getFile")
    
    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8080"))
    debug: bool = os.getenv("DEBUG", "False").strip().lower() in ("true", "1", "yes", "on")
    
    # CORS - Update with your actual frontend domains for production
    cors_origins: list = os.getenv(
    "CORS_ORIGINS", 
    "http://localhost:3000,http://localhost:5173,https://sitespace.vercel.app"
    ).split(",")
    
     # Email settings
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_TLS: bool = True
    FROM_EMAIL: str = "noreply@example.com"
    FROM_NAME: str = "Your App Name"
    
    # Frontend URL for email links
    FRONTEND_URL: str = "http://localhost:3000"
    
    # App settings
    APP_NAME: str = "Sitespace"
    
    # Token expiry settings
    EMAIL_VERIFICATION_EXPIRE_HOURS: int = 24
    PASSWORD_RESET_EXPIRE_HOURS: int = 1
    
    class Config:
        env_file = ".env"

settings = Settings()

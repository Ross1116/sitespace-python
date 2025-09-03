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
    
    # App
    secret_key: str = os.getenv("SECRET_KEY", "PARAGON$87654321")
    export_files_absolute_path: str = os.getenv("EXPORT_FILES_ABSOLUTE_PATH", "/app/uploads/")
    export_files_server_path: str = os.getenv("EXPORT_FILES_SERVER_PATH", "getFile")
    
    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8080"))
    debug: bool = os.getenv("DEBUG", "False").strip().lower() in ("true", "1", "yes", "on")
    
    # CORS - Update with your actual frontend domains for production
    cors_origins: list = os.getenv("CORS_ORIGINS", "https://sitespace.vercel.app").split(",") if os.getenv("CORS_ORIGINS") else ["https://sitespace.vercel.app"]
    
    class Config:
        env_file = ".env"

settings = Settings()

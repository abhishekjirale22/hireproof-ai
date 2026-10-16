import os
import tempfile

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default_factory=lambda: os.getenv("POSTGRES_URL")
        or (f"sqlite:///{os.path.join(tempfile.gettempdir(), 'hireproof.db')}" if os.getenv("VERCEL") else "sqlite:///./hireproof.db")
    )
    frontend_origin: str = "http://localhost:5173"
    max_audio_upload_mb: int = Field(default=20, gt=0, le=100)
    max_api_credit_budget: int = Field(default=1500, ge=0)
    low_credit_warning_threshold: int = Field(default=200, ge=0)
    demo_mode: bool = True
    public_access_mode: bool = True
    detectif_api_key: str = ""
    jwt_secret: str = "change-this-development-secret-before-deployment"
    jwt_expire_minutes: int = 480
    upload_dir: str = os.path.join(tempfile.gettempdir(), "hireproof_uploads") if os.getenv("VERCEL") else "./uploads"
    audio_retention_days: int = Field(default=30, ge=0)
    max_requests_per_minute: int = Field(default=10, gt=0)


settings = Settings()

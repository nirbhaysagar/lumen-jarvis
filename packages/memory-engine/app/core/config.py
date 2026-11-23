"""
Configuration settings for Memory Engine Service.
Loads from environment variables and .env file.
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service Configuration
    SERVICE_NAME: str = "memory-engine"
    ENVIRONMENT: str = "development"
    LUMEN_ENV: str = Field(default_factory=lambda: os.getenv("LUMEN_ENV", "development"))
    HOST: str = "127.0.0.1"
    PORT: int = Field(default_factory=lambda: int(os.getenv("MEMORY_ENGINE_PORT", os.getenv("PORT", "8000"))))
    DEBUG: bool = False
    LUMEN_MEMORY_TOKEN: str | None = Field(default_factory=lambda: os.getenv("LUMEN_MEMORY_TOKEN", None))

    # PostgreSQL Connection (The Hub DB)
    POSTGRES_USER: str = Field(default_factory=lambda: os.getenv("POSTGRES_USER", "lumen"))
    POSTGRES_PASSWORD: str = Field(default_factory=lambda: os.getenv("POSTGRES_PASSWORD", ""))
    POSTGRES_DB: str = Field(default_factory=lambda: os.getenv("POSTGRES_DB", "lumen_memory"))
    POSTGRES_HOST: str = Field(default_factory=lambda: os.getenv("POSTGRES_HOST", "localhost"))
    POSTGRES_PORT: int = Field(default_factory=lambda: int(os.getenv("POSTGRES_PORT", "5432")))

    @property
    def database_url(self) -> str:
        env_url = os.getenv("LUMEN_DATABASE_URL") or os.getenv("DATABASE_URL")
        if env_url:
            return env_url
        db = "lumen_memory_test" if self.LUMEN_ENV == "test" else self.POSTGRES_DB
        pwd = self.POSTGRES_PASSWORD
        if not pwd and self.LUMEN_ENV != "test":
            raise RuntimeError("POSTGRES_PASSWORD must be set in environment when LUMEN_ENV != 'test'")
        return f"postgresql://{self.POSTGRES_USER}:{pwd}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{db}"

    # Embeddings & Local Models
    PREFER_LOCAL_MODELS: bool = Field(default_factory=lambda: os.getenv("PREFER_LOCAL_MODELS", "true").lower() in ("true", "1", "yes"))
    EMBEDDING_DIMENSION: int = 768
    EMBEDDING_MODEL: str = "nomic-embed-text"
    LUMEN_LLM_MODEL: str = "qwen2.5:7b-instruct"
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"


settings = Settings()

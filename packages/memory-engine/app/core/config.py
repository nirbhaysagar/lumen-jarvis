"""
Configuration settings for Memory Engine Service.
Loads from environment variables and .env file.
"""
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
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    # PostgreSQL Connection (The Hub DB)
    POSTGRES_USER: str = Field(default="lumen")
    POSTGRES_PASSWORD: str = Field(default="lumen_secure_pass")
    POSTGRES_DB: str = Field(default="lumen_memory")
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)

    @property
    def database_url(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Embeddings
    EMBEDDING_DIMENSION: int = 1536
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_API_KEY: str | None = None


settings = Settings()

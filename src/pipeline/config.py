from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM ─────────────────────────────────────────────────────────────────
    openai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    default_llm_model: str = "gpt-5.4-mini"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096

    # ── Banco de dados ───────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://pipeline:pipeline@localhost:5432/pipeline"
    database_url_sync: str = "postgresql+psycopg2://pipeline:pipeline@localhost:5432/pipeline"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # ── Langfuse ─────────────────────────────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"
    langfuse_enabled: bool = True

    # ── App ──────────────────────────────────────────────────────────────────
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    @property
    def langfuse_active(self) -> bool:
        return (
            self.langfuse_enabled
            and bool(self.langfuse_public_key)
            and bool(self.langfuse_secret_key)
        )

    def openai_api_key_value(self) -> str:
        return self.openai_api_key.get_secret_value()

    def anthropic_api_key_value(self) -> str:
        return self.anthropic_api_key.get_secret_value()


settings = Settings()

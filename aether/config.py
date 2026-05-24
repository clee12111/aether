# aether/config.py

"""
Central configuration for Aether.

All settings resolve from environment variables or a .env file in the
project root.  Import the module-level ``settings`` singleton everywhere:

    from aether.config import settings
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
"""

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Anthropic ─────────────────────────────────────────────────────────────
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key (required only if a provider == 'anthropic')")

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str | None = Field(default=None, description="OpenAI API key (required only if a provider == 'openai')")
    claude_model: str = Field(
        default="claude-sonnet-4-6",
        description="Model ID used for all agent calls",
    )
    planner_model: str = Field(default="claude-opus-4-5", description="Model for Planner agent")
    critic_model: str = Field(default="claude-haiku-4-5-20251001", description="Model for Critic agent")
    chat_model: str = Field(default="claude-sonnet-4-6", description="Model for Chat interface")

    # ── Provider routing ──────────────────────────────────────────────────────
    planner_provider: str = Field(default="openai", description="planner: 'openai', 'ollama', or 'anthropic'")
    critic_provider: str = Field(default="openai", description="critic: 'openai', 'ollama', or 'anthropic'")
    ollama_base_url: str = Field(default="http://localhost:11434/v1", description="Ollama OpenAI-compatible endpoint")

    # local model names (used when provider == 'ollama')
    planner_model_local: str = Field(default="mistral", description="local planner model")
    critic_model_local: str = Field(default="phi4-mini", description="local critic model")

    # OpenAI model names (used when provider == 'openai')
    planner_model_openai: str = Field(default="gpt-5.4-mini", description="OpenAI planner model")
    critic_model_openai: str = Field(default="gpt-5.4-mini", description="OpenAI critic model")
    max_retries: int = Field(
        default=2,
        ge=1,
        le=10,
        description="LLM call retry limit on validation failure",
    )

    # ── Chroma (local, no Docker) ─────────────────────────────────────────────
    chroma_path: str = Field(
        default="./chroma_db",
        description="Local directory where Chroma persists its data",
    )
    chroma_collection: str = Field(
        default="aether",
        description="Chroma collection name for document chunks",
    )

    # ── Trace store ───────────────────────────────────────────────────────────
    aether_db_path: str = Field(
        default="./aether_trace.db",
        description="SQLite trace database path",
    )

    # ── Embeddings ────────────────────────────────────────────────────────────
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="sentence-transformers model for dense embeddings",
    )

    # ── Reranker ──────────────────────────────────────────────────────────────
    reranker_model: str = Field(
        default="ms-marco-MiniLM-L-12-v2",
        description="flashrank cross-encoder model name",
    )

    # ── Ingestion ─────────────────────────────────────────────────────────────
    chunk_size: int = Field(
        default=512,
        ge=100,
        le=8000,
        description="Max characters per text chunk (PDF / text splitting)",
    )
    chunk_overlap: int = Field(
        default=100,
        ge=0,
        description="Character overlap between consecutive text chunks",
    )
    rows_per_chunk: int = Field(
        default=50,
        ge=1,
        le=5000,
        description="Data rows per chunk for CSV / Excel",
    )

    # ── Retrieval ─────────────────────────────────────────────────────────────
    dense_top_k: int = Field(
        default=20,
        ge=1,
        description="ANN candidates fetched from Chroma per query",
    )
    bm25_top_k: int = Field(
        default=20,
        ge=1,
        description="BM25 candidates fetched per query",
    )
    rerank_top_k: int = Field(
        default=5,
        ge=1,
        description="Final chunk count returned after reranking",
    )

    # ── Prompts ───────────────────────────────────────────────────────────────
    # Swap this to a different domain directory (e.g. "aether/prompts/legal")
    # to change the few-shot examples without editing engine code.
    prompts_dir: str = Field(
        default="aether/prompts/finance",
        description="Directory containing domain-specific prompt templates",
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    aether_log_level: str = Field(default="INFO")

    # ── Data paths ────────────────────────────────────────────────────────────
    data_upload_dir: str = Field(default="./data/uploads")
    data_demo_dir: str = Field(default="./data/demo")

    # ── Cross-field validation ────────────────────────────────────────────────

    @model_validator(mode="after")
    def check_chunk_overlap(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be < "
                f"chunk_size ({self.chunk_size})"
            )
        return self

    @model_validator(mode="after")
    def normalise_log_level(self) -> "Settings":
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = self.aether_log_level.upper()
        if upper not in allowed:
            raise ValueError(
                f"aether_log_level must be one of {allowed}, "
                f"got {self.aether_log_level!r}"
            )
        self.aether_log_level = upper
        return self

    # ── Path helpers ──────────────────────────────────────────────────────────

    @property
    def db_path(self) -> Path:
        """Resolved Path for the SQLite trace database."""
        return Path(self.aether_db_path)

    @property
    def chroma_dir(self) -> Path:
        """Resolved Path for the Chroma persistence directory."""
        return Path(self.chroma_path)

    @property
    def upload_dir(self) -> Path:
        """Resolved Path for the upload directory."""
        return Path(self.data_upload_dir)

    @property
    def demo_dir(self) -> Path:
        """Resolved Path for the demo data directory."""
        return Path(self.data_demo_dir)


# Module-level singleton — import this everywhere
settings = Settings()

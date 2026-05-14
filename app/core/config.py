from __future__ import annotations

import os
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── App ───────────────────────────────────────────────────────────────────
    app_name: str = Field(default="Document Intelligence Platform", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    api_v1_prefix: str = Field(default="/api/v1", alias="API_V1_PREFIX")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    pipeline_version: str = Field(default="0.3.0", alias="PIPELINE_VERSION")

    # ── CORS ──────────────────────────────────────────────────────────────────
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"],
        alias="ALLOWED_ORIGINS",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/docintel",
        alias="DATABASE_URL",
    )
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, alias="DB_POOL_TIMEOUT")
    db_pool_recycle: int = Field(default=1800, alias="DB_POOL_RECYCLE")

    # ── Celery / Redis ────────────────────────────────────────────────────────
    celery_broker_url: str = Field(default="redis://localhost:6379/0", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(
        default="redis://localhost:6379/1", alias="CELERY_RESULT_BACKEND"
    )
    celery_task_soft_time_limit: int = Field(default=300, alias="CELERY_TASK_SOFT_TIME_LIMIT")
    celery_task_time_limit: int = Field(default=600, alias="CELERY_TASK_TIME_LIMIT")

    # ── Storage ───────────────────────────────────────────────────────────────
    storage_backend: Literal["local", "s3"] = Field(default="local", alias="STORAGE_BACKEND")
    upload_dir: Path = Field(default=Path("data/uploads"), alias="UPLOAD_DIR")
    export_dir: Path = Field(default=Path("data/exports"), alias="EXPORT_DIR")
    s3_endpoint_url: str | None = Field(default=None, alias="S3_ENDPOINT_URL")
    s3_access_key_id: str | None = Field(default=None, alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str | None = Field(default=None, alias="S3_SECRET_ACCESS_KEY")
    s3_bucket_uploads: str = Field(default="docintel-uploads", alias="S3_BUCKET_UPLOADS")
    s3_bucket_exports: str = Field(default="docintel-exports", alias="S3_BUCKET_EXPORTS")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")

    # ── OCR ───────────────────────────────────────────────────────────────────
    ocr_engine: str = Field(default="tesseract", alias="OCR_ENGINE")
    spacy_model: str = Field(default="en_core_web_sm", alias="SPACY_MODEL")

    # ── Pipeline ──────────────────────────────────────────────────────────────
    low_confidence_threshold: float = Field(default=0.75, alias="LOW_CONFIDENCE_THRESHOLD")
    max_upload_size_mb: int = Field(default=50, alias="MAX_UPLOAD_SIZE_MB")

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_upload_per_minute: int = Field(default=30, alias="RATE_LIMIT_UPLOAD_PER_MINUTE")
    rate_limit_default_per_minute: int = Field(default=120, alias="RATE_LIMIT_DEFAULT_PER_MINUTE")
    redis_rate_limit_url: str | None = Field(default=None, alias="REDIS_RATE_LIMIT_URL")

    # ── API Auth ──────────────────────────────────────────────────────────────
    api_key_header: str = Field(default="X-API-Key", alias="API_KEY_HEADER")
    api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list, alias="API_KEYS")

    # ── Webhooks ──────────────────────────────────────────────────────────────
    webhook_max_retries: int = Field(default=3, alias="WEBHOOK_MAX_RETRIES")
    webhook_timeout_seconds: int = Field(default=10, alias="WEBHOOK_TIMEOUT_SECONDS")

    # ── Cache ─────────────────────────────────────────────────────────────────
    cache_ttl_seconds: int = Field(default=300, alias="CACHE_TTL_SECONDS")

    # ── LLM Extraction (optional) ─────────────────────────────────────────────
    llm_extraction_enabled: bool = Field(default=False, alias="LLM_EXTRACTION_ENABLED")
    llm_unknown_extraction_enabled: bool = Field(
        default=True, alias="LLM_UNKNOWN_EXTRACTION_ENABLED"
    )

    # ── RAG / Embeddings ──────────────────────────────────────────────────────
    embedding_model: str = Field(default="BAAI/bge-base-en-v1.5", alias="EMBEDDING_MODEL")
    embedding_batch_size: int = Field(default=32, alias="EMBEDDING_BATCH_SIZE")
    chunk_size_chars: int = Field(default=600, alias="CHUNK_SIZE_CHARS")
    chunk_overlap_chars: int = Field(default=120, alias="CHUNK_OVERLAP_CHARS")
    section_detection_enabled: bool = Field(default=True, alias="SECTION_DETECTION_ENABLED")
    retrieval_top_k: int = Field(default=8, alias="RETRIEVAL_TOP_K")
    retrieval_min_score: float = Field(default=0.35, alias="RETRIEVAL_MIN_SCORE")

    # ── Drafting / Preference Learning ────────────────────────────────────────
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    draft_model: str = Field(default="gemini-2.5-flash", alias="DRAFT_MODEL")
    draft_max_chunks: int = Field(default=10, alias="DRAFT_MAX_CHUNKS")
    draft_max_tokens: int = Field(default=8192, alias="DRAFT_MAX_TOKENS")
    preference_dedup_threshold: float = Field(default=0.85, alias="PREFERENCE_DEDUP_THRESHOLD")
    preference_max_per_draft: int = Field(default=5, alias="PREFERENCE_MAX_PER_DRAFT")

    # ── Email Ingestion (optional) ────────────────────────────────────────────
    email_imap_host: str = Field(default="", alias="EMAIL_IMAP_HOST")
    email_imap_port: int = Field(default=993, alias="EMAIL_IMAP_PORT")
    email_address: str = Field(default="", alias="EMAIL_ADDRESS")
    email_password: str = Field(default="", alias="EMAIL_PASSWORD")
    email_folder: str = Field(default="INBOX", alias="EMAIL_FOLDER")
    email_max_attachments_per_run: int = Field(default=50, alias="EMAIL_MAX_ATTACHMENTS_PER_RUN")

    @field_validator("api_keys", mode="before")
    @classmethod
    def parse_api_keys(cls, v: str | list | None) -> list[str]:
        if v in (None, ""):
            return []
        if isinstance(v, str):
            return [k.strip() for k in v.split(",") if k.strip()]
        return list(v)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: str | list | None) -> list[str]:
        if v in (None, ""):
            return ["*"]
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return list(v)

    @model_validator(mode="after")
    def validate_runtime_warnings(self) -> Settings:
        if self.llm_extraction_enabled and not os.environ.get("ANTHROPIC_API_KEY"):
            warnings.warn(
                "LLM_EXTRACTION_ENABLED=true but ANTHROPIC_API_KEY is not set. "
                "LLM fallback extraction will be skipped.",
                stacklevel=2,
            )
        if self.llm_unknown_extraction_enabled and not os.environ.get("ANTHROPIC_API_KEY"):
            warnings.warn(
                "LLM_UNKNOWN_EXTRACTION_ENABLED=true but ANTHROPIC_API_KEY is not set. "
                "Unknown-document LLM extraction will be skipped.",
                stacklevel=2,
            )
        if self.app_env == "production" and self.allowed_origins == ["*"]:
            warnings.warn(
                "ALLOWED_ORIGINS is '*' in production. "
                "Set ALLOWED_ORIGINS to your frontend origin.",
                stacklevel=2,
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if settings.storage_backend == "local":
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        settings.export_dir.mkdir(parents=True, exist_ok=True)
    return settings

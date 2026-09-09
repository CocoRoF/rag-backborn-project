"""Bootstrap settings — environment only. Everything tunable lives in ``system_settings`` (DB)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAGB_", env_file=".env", extra="ignore")

    public_url: str = "http://localhost:3000"
    secret_key: str = Field(default="dev-secret-change-me-dev-secret-change-me")
    encryption_key: str = ""            # Fernet key for DB secrets; derived from secret_key when empty
    encryption_key_previous: str = ""   # comma-separated decrypt-only keys during rotation

    database_url: str = "postgresql+asyncpg://ragb:ragb@localhost:5432/ragb"
    data_dir: Path = Path("/data")

    # Object storage. Empty endpoint keeps blobs on local disk, which is right for one
    # machine and wrong the moment there are two.
    s3_endpoint: str = ""
    s3_bucket: str = "ragb"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    # Seeded administrator so a fresh install has a working console login.
    default_admin_enabled: bool = True
    default_admin_email: str = "admin@ragb.local"
    default_admin_password: str = "admin123"
    default_admin_name: str = "관리자"
    signup_enabled: bool = True

    # Demo accounts. These are seeded at boot AND printed on the login page, which is only
    # ever acceptable because they exist to be shared — a demo whose credentials live in a
    # separate email is a demo nobody opens. Set RAGB_DEMO_MODE=0 on a real deployment and
    # the accounts are neither seeded nor shown.
    demo_mode: bool = True
    demo_accounts: str = (
        "admin@smart.lab:smartlab123:admin:데모 관리자,"
        "test@smart.lab:smatlab123:user:데모 사용자"
    )

    claude_home: Path = Path("/root/.claude")
    claude_binary: str = "claude"
    internal_api_url: str = "http://127.0.0.1:8000"

    log_level: str = "INFO"
    timezone: str = "Asia/Seoul"
    worker_id: str = Field(default_factory=lambda: os.environ.get("HOSTNAME", "worker"))
    access_token_minutes: int = 30
    refresh_token_days: int = 30

    # Untrusted document parsers run in a child process with these ceilings.
    parser_timeout_seconds: int = 30
    parser_memory_mb: int = 768
    parser_max_output_bytes: int = 16 * 1024 * 1024

    fake_llm: bool = False   # tests: deterministic echo model, no network

    @property
    def upload_root(self) -> Path:
        return self.data_dir / "blobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()

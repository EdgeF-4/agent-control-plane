"""Runtime configuration.

All secrets live in ``config.json`` (chmod 600), never in environment files or
the image. The path is taken from ``ACP_CONFIG`` or the first existing default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_PATHS = ("config.json", "/config/config.json")


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8800
    cors_origins: list[str] = Field(default_factory=list)


class DatabaseConfig(BaseModel):
    host: str = "postgres"
    port: int = 5432
    name: str = "controlplane"
    user: str = "controlplane"
    password: str = ""
    # When set, ``url`` wins outright (used by tests to point at SQLite).
    url: str | None = None

    def dsn(self) -> str:
        if self.url:
            return self.url
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class AuthConfig(BaseModel):
    jwt_secret: str
    access_token_ttl_minutes: int = 720
    issuer: str = "agent-control-plane"
    audience: str = "control-plane"


class EnginesConfig(BaseModel):
    cost: dict = Field(default_factory=dict)
    recorder: dict = Field(default_factory=dict)
    siem: dict = Field(default_factory=dict)
    policy: dict = Field(default_factory=lambda: {"default": "allow", "rules": []})
    # Reliability-eval suites and adapter wiring (phase 3). Operators can add
    # their own suites (e.g. an http adapter pointing at their model endpoint).
    eval: dict = Field(default_factory=dict)


class BusConfig(BaseModel):
    """Live event bus. ``memory`` is in-process (single instance); ``redis``
    shares the feed across every instance via Redis pub/sub."""

    backend: str = "memory"
    redis_url: str | None = None
    channel_prefix: str = "acp"


class IngestOAuthConfig(BaseModel):
    """Accept external-IdP JWTs for run ingest, alongside per-project API keys.

    Composes the gateway engine's OAuth2 verifier. RS256 keys resolve from a
    JWKS file or endpoint (cached, rotation-aware); HS256 uses a shared secret
    read from ``hs256_secret_env`` so the secret never lives in this file.
    """

    enabled: bool = False
    algorithm: str = "RS256"  # RS256 (JWKS) | HS256 (shared secret)
    jwks_url: str | None = None
    jwks_path: str | None = None
    jwks_cache_seconds: int = 300
    hs256_secret_env: str | None = None
    issuer: str | None = None
    audience: str | None = None
    client_id_claim: str = "sub"
    roles_claim: str = "roles"
    # Optional claim naming the project slug a token is allowed to ingest into.
    project_claim: str | None = None
    leeway_seconds: int = 30
    # JWT-authenticated agents are attributed to this tenant (by slug).
    tenant_slug: str | None = None


class IngestConfig(BaseModel):
    oauth2: IngestOAuthConfig = Field(default_factory=IngestOAuthConfig)


class AnchorConfig(BaseModel):
    """WORM anchoring of the recorder hash-chain (phase 3).

    Periodically pins a Merkle root over every run's head hash to an append-only,
    itself-hash-chained local file, and optionally to an S3-compatible bucket,
    moving the audit trail from tamper-evident toward non-repudiation.
    """

    enabled: bool = True
    interval_seconds: int = 3600
    # S3-compatible target (MinIO, R2, S3…). Empty ``endpoint_url`` disables it.
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str | None = None
    s3_prefix: str = "anchors"
    s3_access_key_env: str | None = None
    s3_secret_key_env: str | None = None


class BootstrapConfig(BaseModel):
    tenant_slug: str
    tenant_name: str
    admin_email: str
    admin_password: str


class Settings(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    auth: AuthConfig
    data_dir: str = "/data"
    engines: EnginesConfig = Field(default_factory=EnginesConfig)
    bootstrap: BootstrapConfig | None = None
    # How often the background loop checks for due eval schedules (seconds).
    eval_scheduler_seconds: int = 30
    bus: BusConfig = Field(default_factory=BusConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    anchor: AnchorConfig = Field(default_factory=AnchorConfig)

    def engine_path(self, *parts: str) -> str:
        """Resolve a path under ``data_dir`` and make sure its parent exists."""
        p = Path(self.data_dir, *parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)


def find_config_path(explicit: str | None = None) -> str:
    candidates = [explicit] if explicit else []
    candidates.append(os.environ.get("ACP_CONFIG"))
    candidates.extend(DEFAULT_PATHS)
    for c in candidates:
        if c and Path(c).is_file():
            return c
    raise FileNotFoundError(
        "no config.json found; set ACP_CONFIG or copy config.example.json to config.json"
    )


def load_settings(path: str | None = None) -> Settings:
    cfg_path = find_config_path(path)
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return Settings(**raw)

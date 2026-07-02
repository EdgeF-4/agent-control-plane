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

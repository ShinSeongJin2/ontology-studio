"""Runtime settings shared across backend modules."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    container_name: str
    sandbox_workdir: str
    openai_model: str
    openai_base_url: str
    openai_api_key: str
    openai_reasoning_effort: str
    backend_host: str
    backend_port: int
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings(
        container_name=os.environ.get("CONTAINER_NAME", "deepagents-sandbox"),
        sandbox_workdir=os.environ.get("SANDBOX_WORKDIR", "/workspace"),
        openai_model=os.environ.get("OPENAI_MODEL", "openai:gpt-5.4-2026-03-05"),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_reasoning_effort=os.environ.get("OPENAI_REASONING_EFFORT", "medium"),
        backend_host=os.environ.get("BACKEND_HOST", "0.0.0.0"),
        backend_port=int(os.environ.get("BACKEND_PORT", "8000")),
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("NEO4J_PASSWORD", ""),
    )

"""Server settings, read from the same `.env` as the engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def _split(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


@dataclass
class ServerSettings:
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = field(default_factory=list)
    workspace_root: Path = REPO_ROOT / "workspaces"
    target_repo_url: str = ""
    target_repo_ref: str = "main"
    mongodb_uri: str = ""
    mongodb_db: str = "synesis"
    replay_fixture: str = ""

    @classmethod
    def from_env(cls) -> "ServerSettings":
        workspace_root = Path(os.getenv("WORKSPACE_ROOT", str(REPO_ROOT / "workspaces")))
        if not workspace_root.is_absolute():
            workspace_root = (REPO_ROOT / workspace_root).resolve()

        return cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            # Next's dev server is on 3000. The frontend calls this API directly
            # rather than proxying, so its origin must be listed here or every
            # request fails in the browser with a CORS error.
            cors_origins=_split("CORS_ORIGINS", "http://localhost:3000"),
            workspace_root=workspace_root,
            target_repo_url=os.getenv("TARGET_REPO_URL", "").strip(),
            target_repo_ref=os.getenv("TARGET_REPO_REF", "main").strip() or "main",
            mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
            mongodb_db=os.getenv("MONGODB_DB", "synesis").strip() or "synesis",
            # Set REPLAY_FIXTURE to serve fixtures/stream.jsonl instead of live agents.
            # This is the backup demo — no keys, no latency, no surprises on stage.
            replay_fixture=os.getenv("REPLAY_FIXTURE", "").strip(),
        )

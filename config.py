"""Central configuration, read from environment variables (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Viewport:
    name: str
    width: int
    height: int
    mobile: bool = False


def parse_viewports(spec: str) -> list[Viewport]:
    """'name:WxH[:m],...'  (':m' marks a touch/mobile emulation viewport)."""
    out: list[Viewport] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        w, h = bits[1].lower().split("x")
        out.append(Viewport(bits[0], int(w), int(h), len(bits) > 2 and bits[2] == "m"))
    return out


DEFAULT_VIEWPORTS = "iphone-se:320x568:m,iphone-14:375x812:m,ipad:768x1024:m,desktop:1440x900"


@dataclass
class Settings:
    vlm: str = field(default_factory=lambda: os.getenv("OMNISIGHT_VLM", "auto"))
    openai_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OMNISIGHT_OPENAI_MODEL", "gpt-4o"))
    anthropic_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: os.getenv("OMNISIGHT_ANTHROPIC_MODEL", "claude-sonnet-4-5"))
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OMNISIGHT_OLLAMA_MODEL", "llava"))
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    webhook_secret: str = field(default_factory=lambda: os.getenv("GITHUB_WEBHOOK_SECRET", ""))
    css_path: str = field(default_factory=lambda: os.getenv("OMNISIGHT_CSS_PATH", "src/omnisight-fixes.css"))
    auto_merge: bool = field(default_factory=lambda: os.getenv("OMNISIGHT_AUTO_MERGE_ON_APPROVE", "false").lower() == "true")
    api_key: str = field(default_factory=lambda: os.getenv("OMNISIGHT_API_KEY", ""))
    public_url: str = field(default_factory=lambda: os.getenv("OMNISIGHT_PUBLIC_URL", "http://localhost:8000"))
    runs_dir: Path = field(default_factory=lambda: Path(os.getenv("OMNISIGHT_RUNS_DIR", "runs")))
    max_attempts: int = field(default_factory=lambda: int(os.getenv("OMNISIGHT_MAX_ATTEMPTS", "3")))
    viewports: list[Viewport] = field(default_factory=lambda: parse_viewports(os.getenv("OMNISIGHT_VIEWPORTS", DEFAULT_VIEWPORTS)))

    @property
    def dry_run(self) -> bool:
        return not self.github_token


def get_settings() -> Settings:
    return Settings()

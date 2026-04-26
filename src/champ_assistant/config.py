"""App configuration (paths, timeouts, feature flags).

Phase 0 skeleton. Will be filled out alongside concrete consumers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Timeouts:
    http_seconds: float = 5.0
    websocket_seconds: float = 10.0
    claude_seconds: float = 15.0


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    cache_dir: Path
    timeouts: Timeouts = Timeouts()
    log_level: str = "INFO"

"""Static-data loaders for counters / tiers / tags / strategies.

Phase 3 module.
"""
from __future__ import annotations

from pathlib import Path


def load_counters(path: Path) -> dict[str, object]:
    raise NotImplementedError("Phase 3")


def load_tiers(path: Path) -> dict[str, object]:
    raise NotImplementedError("Phase 3")


def load_tags(path: Path) -> dict[str, object]:
    raise NotImplementedError("Phase 3")

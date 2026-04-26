"""Static JSON loaders.

Reads the project's ``data/counters.json``, ``data/tiers.json`` and
``data/tags.json`` and validates them via Pydantic. Sync API — these are
read once at app startup (or on a manual refresh), never on the hot path.
"""
from __future__ import annotations

from pathlib import Path

from .models import CounterMatrix, TagsData, TierList


class DataLoadError(Exception):
    """Raised when a static data file is missing or fails validation."""


def _read_json_text(path: Path) -> str:
    if not path.is_file():
        raise DataLoadError(f"Data file not found: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataLoadError(f"Could not read {path}: {exc}") from exc


def load_counters(path: Path) -> CounterMatrix:
    text = _read_json_text(path)
    try:
        return CounterMatrix.model_validate_json(text)
    except Exception as exc:
        raise DataLoadError(f"Invalid counters file {path}: {exc}") from exc


def load_tiers(path: Path) -> TierList:
    text = _read_json_text(path)
    try:
        return TierList.model_validate_json(text)
    except Exception as exc:
        raise DataLoadError(f"Invalid tiers file {path}: {exc}") from exc


def load_tags(path: Path) -> TagsData:
    text = _read_json_text(path)
    try:
        return TagsData.model_validate_json(text)
    except Exception as exc:
        raise DataLoadError(f"Invalid tags file {path}: {exc}") from exc

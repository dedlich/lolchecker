"""Tests for the static-data JSON loaders.

Also covers the production data/*.json files as smoke tests so a typo in
counters.json fails CI immediately.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from champ_assistant.data.loader import (
    DataLoadError,
    load_counters,
    load_tags,
    load_tiers,
)


REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "static"


# ---------------------------------------------------------------------------
# load_counters
# ---------------------------------------------------------------------------

def test_load_counters_valid(tmp_path: Path) -> None:
    f = tmp_path / "counters.json"
    f.write_text(
        json.dumps(
            {
                "patch": "14.8",
                "matrix": {
                    "Garen": {
                        "TOP": [{"champion": "Darius", "score": 8.0, "tier": "S"}]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cm = load_counters(f)
    assert cm.patch == "14.8"
    assert cm.counters_for("Garen", "TOP")[0].champion == "Darius"


def test_load_counters_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DataLoadError, match="not found"):
        load_counters(tmp_path / "nope.json")


def test_load_counters_invalid_json(tmp_path: Path) -> None:
    f = tmp_path / "counters.json"
    f.write_text("not json", encoding="utf-8")
    with pytest.raises(DataLoadError, match="Invalid"):
        load_counters(f)


def test_load_counters_schema_violation(tmp_path: Path) -> None:
    f = tmp_path / "counters.json"
    f.write_text(
        json.dumps(
            {"matrix": {"Garen": {"TOP": [{"champion": "Darius", "score": 99.0}]}}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(DataLoadError):
        load_counters(f)


def test_load_counters_empty_matrix(tmp_path: Path) -> None:
    f = tmp_path / "counters.json"
    f.write_text(json.dumps({"patch": None, "matrix": {}}), encoding="utf-8")
    cm = load_counters(f)
    assert cm.matrix == {}


# ---------------------------------------------------------------------------
# load_tiers / load_tags
# ---------------------------------------------------------------------------

def test_load_tiers_valid(tmp_path: Path) -> None:
    f = tmp_path / "tiers.json"
    f.write_text(
        json.dumps(
            {"patch": None, "tiers": {"TOP": [{"champion": "Darius", "tier": "S+"}]}}
        ),
        encoding="utf-8",
    )
    tl = load_tiers(f)
    assert tl.tier_for("Darius", "TOP") == "S+"


def test_load_tiers_invalid_role(tmp_path: Path) -> None:
    f = tmp_path / "tiers.json"
    f.write_text(
        json.dumps({"tiers": {"MIDDLE": [{"champion": "X", "tier": "S"}]}}),
        encoding="utf-8",
    )
    with pytest.raises(DataLoadError):
        load_tiers(f)


def test_load_tags_valid(tmp_path: Path) -> None:
    f = tmp_path / "tags.json"
    f.write_text(
        json.dumps({"tags": {"Garen": ["Fighter", "Tank"]}}), encoding="utf-8"
    )
    td = load_tags(f)
    assert td.tags_for("Garen") == ["Fighter", "Tank"]


# ---------------------------------------------------------------------------
# Production data files
# ---------------------------------------------------------------------------

def test_production_counters_file_loads() -> None:
    cm = load_counters(REPO_DATA_DIR / "counters.json")
    # At least one demo entry should be present.
    assert "Garen" in cm.matrix


def test_production_tiers_file_loads() -> None:
    tl = load_tiers(REPO_DATA_DIR / "tiers.json")
    assert "TOP" in tl.tiers
    assert any(e.champion == "Darius" for e in tl.tiers["TOP"])


def test_production_tags_file_loads() -> None:
    td = load_tags(REPO_DATA_DIR / "tags.json")
    assert "Fighter" in td.tags_for("Darius")

"""Tests for ChampionBuild + BuildLibrary models and loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from champ_assistant.data.loader import DataLoadError, load_builds
from champ_assistant.data.models import BuildLibrary, ChampionBuild


def test_build_library_lookup_returns_match() -> None:
    lib = BuildLibrary(
        builds={
            "Darius": {
                "TOP": ChampionBuild(
                    runes=["Conqueror", "Triumph"],
                    items=["Stridebreaker", "Sterak's Gage"],
                    summoners=["Flash", "Teleport"],
                )
            }
        }
    )
    build = lib.build_for("Darius", "TOP")
    assert build is not None
    assert "Conqueror" in build.runes
    assert "Stridebreaker" in build.items


def test_build_library_lookup_returns_none_for_unknown() -> None:
    lib = BuildLibrary()
    assert lib.build_for("Whoever", "TOP") is None


def test_load_builds_valid(tmp_path: Path) -> None:
    f = tmp_path / "builds.json"
    f.write_text(
        json.dumps(
            {
                "patch": "14.8",
                "builds": {
                    "Yasuo": {
                        "MID": {
                            "runes": ["Lethal Tempo"],
                            "items": ["Immortal Shieldbow"],
                            "summoners": ["Flash", "Ignite"],
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    lib = load_builds(f)
    assert lib.patch == "14.8"
    assert lib.build_for("Yasuo", "MID").items == ["Immortal Shieldbow"]


def test_load_builds_invalid_role(tmp_path: Path) -> None:
    f = tmp_path / "builds.json"
    f.write_text(
        json.dumps(
            {"builds": {"Yasuo": {"MIDDLE": {"runes": [], "items": [], "summoners": []}}}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(DataLoadError):
        load_builds(f)


def test_production_builds_file_loads() -> None:
    repo_data = Path(__file__).resolve().parents[2] / "static"
    lib = load_builds(repo_data / "builds.json")
    # We seeded ~30 champions in v0.4.0.
    assert len(lib.builds) >= 25
    # Spot check a known entry.
    darius_top = lib.build_for("Darius", "TOP")
    assert darius_top is not None
    assert "Conqueror" in darius_top.runes

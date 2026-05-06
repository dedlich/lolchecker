"""LoL Champ Select Assistant.

The version is sourced once from ``pyproject.toml`` so we don't have
to keep two literals in sync. Resolution order:

1. **Source-tree / editable install** — read ``pyproject.toml`` directly
   from the repo root (two parents up from this file). Always current
   without re-running ``pip install -e .``.
2. **Regular pip install / PyInstaller bundle** — pyproject.toml isn't
   present in the install location, so fall back to
   ``importlib.metadata.version("champ-assistant")``. PyInstaller bundles
   ship the dist-info, so this works in the frozen exe.
3. **Last resort** — neither succeeded (e.g. ad-hoc invocation in a
   stripped environment): ``"0.0.0+local"``.
"""
from __future__ import annotations


def _resolve_version() -> str:
    """Resolve the package version. Source tree wins over installed
    metadata so dev iterations don't need a ``pip install -e .`` after
    every bump."""
    try:
        from pathlib import Path
        import tomllib
        _pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if _pyproject.is_file():
            with _pyproject.open("rb") as fh:
                return tomllib.load(fh)["project"]["version"]
    except Exception:  # noqa: BLE001 — never let a metadata read crash import
        pass
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("champ-assistant")
    except Exception:  # noqa: BLE001 — same: degrade rather than crash
        return "0.0.0+local"


__version__ = _resolve_version()

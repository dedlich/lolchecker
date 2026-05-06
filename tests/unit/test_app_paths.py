"""Tests for app_paths — single source of truth for resolved app dirs."""
from __future__ import annotations

from pathlib import Path

from champ_assistant import app_paths


# ---------------------------------------------------------------------------
# Cross-platform user-data root
# ---------------------------------------------------------------------------

def test_user_data_root_on_windows_uses_localappdata(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = app_paths._user_data_root()
    assert root == tmp_path / "ChampAssistant"


def test_user_data_root_on_windows_falls_back_to_home(monkeypatch, tmp_path) -> None:
    """Missing %LOCALAPPDATA% — fall back to ~/AppData/Local."""
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = app_paths._user_data_root()
    assert root == tmp_path / "AppData" / "Local" / "ChampAssistant"


def test_user_data_root_on_unix_uses_dotdir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = app_paths._user_data_root()
    assert root == tmp_path / ".champ-assistant"


# ---------------------------------------------------------------------------
# Public API: log_dir, state_dir, resource_root
# ---------------------------------------------------------------------------

def test_log_dir_is_logs_under_user_data_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert app_paths.log_dir() == tmp_path / ".champ-assistant" / "logs"


def test_state_dir_equals_user_data_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert app_paths.state_dir() == tmp_path / ".champ-assistant"


def test_resource_root_returns_meipass_when_frozen(monkeypatch, tmp_path) -> None:
    """PyInstaller sets sys.frozen + sys._MEIPASS — resource_root should
    return the bundle path, not the source tree."""
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    assert app_paths.resource_root() == tmp_path


def test_resource_root_returns_repo_root_in_dev() -> None:
    """In source-tree dev (no sys.frozen), resource_root walks up to the
    repo root. Verify by spotting a known top-level file (pyproject.toml)."""
    root = app_paths.resource_root()
    assert (root / "pyproject.toml").is_file(), (
        f"resource_root() returned {root} but pyproject.toml isn't there"
    )


# ---------------------------------------------------------------------------
# Path-resolution is pure (no I/O, no mkdir)
# ---------------------------------------------------------------------------

def test_path_functions_do_not_create_directories(monkeypatch, tmp_path) -> None:
    """app_paths is path-resolution only — actual mkdir is the caller's
    job. Calling the functions on an empty tmp_path must not create dirs."""
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    app_paths.log_dir()
    app_paths.state_dir()
    # Nothing should have been created under tmp_path.
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Backwards compat: existing delegators
# ---------------------------------------------------------------------------

def test_performance_monitor_log_dir_delegates(monkeypatch, tmp_path) -> None:
    """``performance_monitor._log_dir`` is the legacy entry point — it must
    return the same value as ``app_paths.log_dir`` so existing tests that
    monkeypatch it stay coherent with the new module."""
    from champ_assistant import performance_monitor as pm
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert pm._log_dir() == app_paths.log_dir()

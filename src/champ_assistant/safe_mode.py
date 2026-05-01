"""Safe Mode startup detection + clean-shutdown marker.

Two on-disk markers next to ``crash_report.json``:

  * ``crash_report.json``    — present iff the previous session crashed
                                (written by safety.py / lifecycle.py)
  * ``clean_shutdown.marker`` — present iff the previous shutdown ran
                                LifecycleManager.shutdown() to completion

The combination decides startup mode:

  +------------------+------------------+----------------------+
  | crash_report.    | clean_shutdown.  | mode                 |
  | json             | marker           |                      |
  +------------------+------------------+----------------------+
  | absent           | (any)            | NORMAL               |
  | present          | present          | NORMAL — likely a    |
  |                  |                  | prior crash already  |
  |                  |                  | resolved manually    |
  | present          | absent           | SAFE                 |
  +------------------+------------------+----------------------+

In Safe Mode the app boots with hotkeys + telemetry + update-checks
disabled (anything that touches the OS or the network and could be
implicated in a crash loop); UI, diagnostics, and layout persistence
remain on so the user can actually use the app to fix the underlying
issue. Clicking "Resume Normal Mode" deletes the crash report, writes
the marker, and the next start is normal again.

This module is deliberately Qt-free so it can be unit-tested without a
QApplication. Wire-up (setting the dataclass into ``__main__`` and
gating subsystems on its flags) is in ``__main__.py``.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .crash_report import clear_crash_report, crash_report_path

logger = logging.getLogger(__name__)


def _state_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant"
    return Path.home() / ".champ-assistant"


def clean_shutdown_marker_path() -> Path:
    return _state_dir() / "clean_shutdown.marker"


@dataclass(frozen=True)
class StartupMode:
    """The result of ``decide_startup_mode``. The ``__main__`` wiring
    reads ``safe`` to gate optional subsystems."""
    safe: bool
    reason: str  # human-readable explanation for logs / banner


def decide_startup_mode(
    *,
    crash_path: Path | None = None,
    marker_path: Path | None = None,
) -> StartupMode:
    """Inspect the two disk markers and decide. Pure function for
    testability — both paths are injectable."""
    crash_p = crash_path or crash_report_path()
    marker_p = marker_path or clean_shutdown_marker_path()
    has_crash = crash_p.is_file()
    has_marker = marker_p.is_file()

    if not has_crash:
        return StartupMode(safe=False, reason="no prior crash report")
    if has_marker:
        # User restarted after a clean shutdown that followed an old
        # crash — assume the issue's resolved. Still nuke the crash
        # report so we don't carry it forever.
        return StartupMode(
            safe=False,
            reason="crash report present but clean shutdown followed",
        )
    return StartupMode(
        safe=True,
        reason="prior session crashed — booting in Safe Mode",
    )


def consume_clean_shutdown_marker(marker_path: Path | None = None) -> None:
    """Delete the marker on startup. Calling this every launch keeps
    the marker semantically "did the LAST shutdown run cleanly" — if
    the upcoming session crashes the marker won't be there to mask it.
    """
    target = marker_path or clean_shutdown_marker_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.info("safe_mode: marker delete failed: %s", exc)


def write_clean_shutdown_marker(marker_path: Path | None = None) -> bool:
    """Atomically write an empty marker file at the end of a clean
    shutdown. Returns True on success, False on failure. Never raises."""
    target = marker_path or clean_shutdown_marker_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(target.parent),
            prefix=".clean_shutdown.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            fh.write("")  # empty content; existence is the signal
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
            tmp_path = Path(fh.name)
        os.replace(str(tmp_path), str(target))
        return True
    except Exception:  # noqa: BLE001
        logger.warning("safe_mode: clean_shutdown marker write failed")
        return False


def resume_normal_mode(
    *,
    crash_path: Path | None = None,
    marker_path: Path | None = None,
) -> None:
    """Action behind the "Resume Normal Mode" button: drop the crash
    report and write the marker. Next start is normal regardless of
    whether THIS shutdown is clean (covers the case where the user
    clicks Resume and the app then immediately crashes — they still
    boot normal next time so they can take a different action)."""
    clear_crash_report(crash_path)
    write_clean_shutdown_marker(marker_path)

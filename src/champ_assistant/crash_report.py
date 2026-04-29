"""Persisted crash report for failure-recovery diagnostics.

Writes a single ``crash_report.json`` next to the rest of the app's
persisted state whenever an unhandled exception fires through
``sys.excepthook``, the asyncio loop's exception handler, or a fatal
LifecycleManager failure. The file is the breadcrumb the next launch
reads to decide between Normal and Safe Mode.

Hard contract
=============
  * ``write_crash_report`` MUST NEVER raise. Failure to write is logged
    and swallowed — re-raising from inside ``sys.excepthook`` would
    abort the interpreter with a traceback the user can never read.
  * Atomic write via temp-file + ``os.replace`` so a partial-file
    state is impossible (Windows MoveFileEx is atomic for a same-
    volume rename, POSIX rename is atomic).
  * Output is capped at ~32 KB — traceback is the only large field
    and gets truncated head-tail with a marker if needed.
  * Tolerant of partial app-state inputs: missing fields fall back
    to ``None``/empty defaults rather than crashing the writer.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

logger = logging.getLogger(__name__)

# Hard cap on the on-disk size of the crash report. The fixed fields
# (timestamp, version, platform, etc.) are well under 1 KB combined;
# the traceback is the variable-length payload that we truncate.
MAX_FILE_BYTES = 32 * 1024
MAX_TRACEBACK_BYTES = 24 * 1024  # leaves headroom for the rest


def _state_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant"
    return Path.home() / ".champ-assistant"


def crash_report_path() -> Path:
    return _state_dir() / "crash_report.json"


def _truncate_traceback(text: str, *, limit: int = MAX_TRACEBACK_BYTES) -> str:
    """If ``text`` exceeds ``limit`` bytes, keep head + tail with a
    marker in between. The first frames usually point at the entry,
    the last frames at the failure — the middle of a 200-frame async
    traceback is the part nobody reads."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    head = encoded[: limit // 2].decode("utf-8", errors="replace")
    tail = encoded[-limit // 2 :].decode("utf-8", errors="replace")
    omitted = len(encoded) - limit
    return (
        f"{head}\n\n"
        f"... [truncated {omitted} bytes from the middle of the traceback] ...\n\n"
        f"{tail}"
    )


def _format_exception(
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    exc_tb: TracebackType | None,
) -> dict[str, str]:
    """Render an exception triple to the ``{type, message, traceback}``
    shape used in the report. All three keys are always present even
    if the input is ``None`` so the consuming summary code never has
    to defend against missing keys."""
    if exc_type is None and exc_value is None:
        return {"type": "", "message": "", "traceback": ""}
    type_name = exc_type.__name__ if exc_type else type(exc_value).__name__
    message = str(exc_value) if exc_value is not None else ""
    if exc_tb is not None:
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    elif exc_value is not None:
        tb_text = "".join(traceback.format_exception(type(exc_value), exc_value, None))
    else:
        tb_text = ""
    return {
        "type": type_name,
        "message": message[:1000],   # over-long messages are usually noise
        "traceback": _truncate_traceback(tb_text),
    }


def build_report(
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    exc_tb: TracebackType | None,
    *,
    version: str,
    uptime_seconds: float,
    phase: str | None = None,
    connection_state: str | None = None,
    active_widgets: list[str] | None = None,
    last_state_vector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the report dict. Never raises — every input is a
    plain primitive or list/dict, defensively coerced to safe defaults
    if missing or wrong-typed."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": str(version),
        "platform": sys.platform,
        "python_version": sys.version,
        "uptime_seconds": float(uptime_seconds) if isinstance(uptime_seconds, (int, float)) else 0.0,
        "phase": phase or "",
        "connection_state": connection_state or "",
        "active_widgets": list(active_widgets) if active_widgets else [],
        "last_state_vector": dict(last_state_vector) if last_state_vector else {},
        "exception": _format_exception(exc_type, exc_value, exc_tb),
    }


# Type alias for the application-state collector callback. Returns a
# (possibly-partial) dict the caller plugs into ``write_crash_report``.
StateCollector = Callable[[], dict[str, Any]]


def write_crash_report(
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    exc_tb: TracebackType | None,
    *,
    version: str = "0.0.0",
    uptime_seconds: float = 0.0,
    state_collector: StateCollector | None = None,
    path: Path | None = None,
) -> Path | None:
    """Write the crash report atomically. Returns the written path on
    success, ``None`` on failure. Never raises.

    The ``state_collector`` callback is invoked once and its output
    merged into the report — we wrap the call in try/except because a
    misbehaving collector during a crash is the last thing we want to
    bubble up.
    """
    target = path or crash_report_path()
    state: dict[str, Any] = {}
    if state_collector is not None:
        try:
            state = state_collector() or {}
        except Exception:  # noqa: BLE001 — tolerant of partial data
            logger.warning("crash_report: state_collector raised — using empty state")
            state = {}

    try:
        report = build_report(
            exc_type, exc_value, exc_tb,
            version=version,
            uptime_seconds=uptime_seconds,
            phase=state.get("phase"),
            connection_state=state.get("connection_state"),
            active_widgets=state.get("active_widgets"),
            last_state_vector=state.get("last_state_vector"),
        )
        body = json.dumps(report, indent=2, default=str)
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            # Last-resort: drop the verbose fields, keep the essentials.
            report["exception"]["traceback"] = (
                report["exception"]["traceback"][: MAX_TRACEBACK_BYTES // 4]
                + "\n... [further truncated to fit 32KB cap] ..."
            )
            body = json.dumps(report, indent=2, default=str)
    except Exception:  # noqa: BLE001
        logger.exception("crash_report: build_report failed — skipping write")
        return None

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file in the same directory, fsync, replace.
        # tempfile.NamedTemporaryFile keeps the fd open; we close after
        # writing then os.replace for the cross-platform-atomic swap.
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(target.parent),
            prefix=".crash_report.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as fh:
            fh.write(body)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass  # fsync not always supported (e.g. some Windows tmpfs)
            tmp_path = Path(fh.name)
        os.replace(str(tmp_path), str(target))
        return target
    except Exception:  # noqa: BLE001
        logger.exception("crash_report: write failed — diagnostics may be missing on next start")
        return None


def has_crash_report(path: Path | None = None) -> bool:
    """True iff a crash report exists on disk. Used by safe-mode
    detection at startup."""
    return (path or crash_report_path()).is_file()


def clear_crash_report(path: Path | None = None) -> None:
    """Remove the on-disk report. Silent no-op if missing or
    unremovable — caller already tolerates failure."""
    target = path or crash_report_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.info("crash_report: clear failed: %s", exc)


def read_crash_report(path: Path | None = None) -> dict[str, Any] | None:
    """Best-effort read of the on-disk report for diagnostics display.
    Returns ``None`` on any failure (missing file, corrupt JSON,
    permission error)."""
    target = path or crash_report_path()
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

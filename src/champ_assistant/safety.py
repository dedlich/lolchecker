"""Crash prevention layer.

Phase 0 skeleton. Phase 1 wires up the global exception handler with tests.
The CrashHandler must NEVER call sys.exit — degrade, log, surface to UI.
"""
from __future__ import annotations

from typing import Any


class CrashHandler:
    """Global exception sink for sync + asyncio paths.

    Real implementation (Phase 1) installs sys.excepthook and the asyncio
    exception handler, and emits a Qt signal so the UI can show a toast.
    """

    def install(self) -> None:
        raise NotImplementedError("Phase 1")

    def _handle(self, exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        raise NotImplementedError("Phase 1")

    def _handle_async(self, loop: Any, context: dict[str, Any]) -> None:
        raise NotImplementedError("Phase 1")

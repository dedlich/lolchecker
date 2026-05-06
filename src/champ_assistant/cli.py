"""Argparse construction.

Lifted out of ``__main__`` per OPTIMIZATION.md §3.3 so the entry-point
narrative (``parse_args() → build_runtime() → run() → exit()``) stays
small. ``__main__`` re-exports ``build_parser`` for backwards compat
with existing call sites.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from champ_assistant import app_paths

DEFAULT_FIXTURE_DIR = app_paths.resource_root() / "tests" / "fixtures" / "sessions"
DEFAULT_DATA_DIR = app_paths.resource_root() / "data"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="champ-assistant",
        description="LoL Champ Select Assistant — counters & pick suggestions.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without a real League client; replay JSON fixtures.")
    parser.add_argument("--fixture", type=Path, default=None,
                        help=f"Fixture file or directory (default: {DEFAULT_FIXTURE_DIR})")
    parser.add_argument("--cycle", action="store_true",
                        help="Cycle through all fixtures (with --dry-run).")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="Seconds between fixture cycles (default: 5).")
    parser.add_argument("--stress", action="store_true",
                        help="Emit randomized state updates (with --dry-run).")
    parser.add_argument("--rate", type=float, default=10.0,
                        help="Stress-mode update rate in Hz (default: 10).")
    parser.add_argument("--demo-recommendations", action="store_true",
                        help="Render all decision-engine rules in the "
                             "recommendation panel for visual testing "
                             "(no live game required).")
    parser.add_argument("--no-ui", action="store_true",
                        help="Skip the Qt window (print events instead).")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                        help=f"Static data directory (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    # Bootstrap installer args — hidden from --help; set by apply_update when
    # launching the newly extracted exe to install itself.
    parser.add_argument("--bootstrap-staged", metavar="DIR", help=argparse.SUPPRESS)
    parser.add_argument("--bootstrap-install", metavar="DIR", help=argparse.SUPPRESS)
    parser.add_argument("--bootstrap-parent-pid", type=int, metavar="PID",
                        help=argparse.SUPPRESS)
    return parser

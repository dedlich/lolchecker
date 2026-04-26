"""CLI entry point.

Phase 0 scaffolding: argument parsing only. The actual app wiring (LCU source,
UI bootstrap, qasync loop) lands in later phases.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="champ-assistant",
        description="LoL Champ Select Assistant — counters & pick suggestions.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without a real League client; replay fixtures via FixtureLcuSource.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="Path to a champ-select session JSON fixture (used with --dry-run).",
    )
    parser.add_argument(
        "--cycle",
        action="store_true",
        help="Cycle through all fixtures in the directory (with --dry-run).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between fixture cycles (default: 5).",
    )
    parser.add_argument(
        "--stress",
        action="store_true",
        help="Emit randomized state updates at high frequency (with --dry-run).",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=10.0,
        help="Stress-mode update rate in Hz (default: 10).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console log level (default: INFO).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.dry_run:
        print(
            f"[champ-assistant] dry-run mode "
            f"(fixture={args.fixture}, cycle={args.cycle}, stress={args.stress})"
        )
    else:
        print("[champ-assistant] live mode — LCU bootstrap not yet implemented (Phase 2).")

    return 0


if __name__ == "__main__":
    sys.exit(main())

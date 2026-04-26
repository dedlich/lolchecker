"""CLI entry point.

Phase 2 wires ``--dry-run`` to FixtureLcuSource so you can stream events from
the JSON fixtures end-to-end on macOS. The full UI bootstrap and live LCU
pipeline land in Phase 6 (Integration).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

from .lcu.sources import FixtureLcuSource, LcuSource, RealLcuSource

DEFAULT_FIXTURE_DIR = Path("tests/fixtures/sessions")


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
        help=(
            "Path to a champ-select session JSON fixture or a directory of "
            f"fixtures (used with --dry-run). Default: {DEFAULT_FIXTURE_DIR}"
        ),
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


def _make_source(args: argparse.Namespace) -> LcuSource:
    if args.dry_run:
        fixture = args.fixture or DEFAULT_FIXTURE_DIR
        return FixtureLcuSource(
            fixture,
            cycle=args.cycle,
            stress=args.stress,
            interval=args.interval,
            rate=args.rate,
        )
    return RealLcuSource()


async def _stream_events(source: LcuSource) -> None:
    loop = asyncio.get_running_loop()
    stop_signal = asyncio.Event()

    def _request_stop() -> None:
        stop_signal.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows signals are limited; the default KeyboardInterrupt path
            # still works there.
            pass

    consumer = asyncio.create_task(_consume(source))
    stopper = asyncio.create_task(stop_signal.wait())
    done, _ = await asyncio.wait(
        {consumer, stopper}, return_when=asyncio.FIRST_COMPLETED
    )
    if stopper in done:
        await source.close()
    await consumer


async def _consume(source: LcuSource) -> None:
    async for event in source.events():
        # Single-line JSON per event so the output is grep/jq-friendly during dev.
        print(json.dumps(_summarize(event), default=str), flush=True)


def _summarize(event: dict[str, object]) -> dict[str, object]:
    """Trim verbose session payloads for console output (full data still in the source)."""
    if event.get("type") != "session":
        return event
    data = event.get("data") or {}
    if not isinstance(data, dict):
        return event
    return {
        "type": "session",
        "phase": data.get("phase"),
        "localPlayerCellId": data.get("localPlayerCellId"),
        "myTeam": [
            {"cellId": p.get("cellId"), "championId": p.get("championId"),
             "assignedPosition": p.get("assignedPosition")}
            for p in (data.get("myTeam") or [])
            if isinstance(p, dict)
        ],
        "theirTeam": [
            {"cellId": p.get("cellId"), "championId": p.get("championId")}
            for p in (data.get("theirTeam") or [])
            if isinstance(p, dict)
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.dry_run:
        print(
            "[champ-assistant] live mode — UI + event-stream wiring lands in Phase 6.",
            file=sys.stderr,
        )
        return 0

    source = _make_source(args)
    try:
        asyncio.run(_stream_events(source))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

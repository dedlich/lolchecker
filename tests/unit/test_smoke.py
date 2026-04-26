"""Phase 0 smoke test: package imports and CLI parses --dry-run."""
from __future__ import annotations

import champ_assistant
from champ_assistant.__main__ import build_parser


def test_package_has_version() -> None:
    assert isinstance(champ_assistant.__version__, str)
    assert champ_assistant.__version__


def test_cli_parses_dry_run() -> None:
    parser = build_parser()
    args = parser.parse_args(["--dry-run"])
    assert args.dry_run is True
    assert args.cycle is False
    assert args.stress is False


def test_cli_defaults_live_mode() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    assert args.dry_run is False
    assert args.fixture is None
    assert args.interval == 5.0
    assert args.rate == 10.0

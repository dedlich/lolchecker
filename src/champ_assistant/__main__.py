"""CLI entry point.

The imperative narrative: ``parse_args() → bootstrap-install short-circuit
→ logging setup → headless or UI run → exit``. Everything heavier lives in
sibling modules per OPTIMIZATION.md §3.3:

  * ``cli.py``                — argparse construction + DEFAULT_*
  * ``runtime_factory.py``    — ``_build_assistant`` + supporting factories
  * ``boot.py``               — Qt setup, async loops, file logger, dotenv
  * ``bootstrap_installer.py`` — the hidden ``--bootstrap-install`` mode

Tests + external code that import ``from champ_assistant.__main__ import …``
keep working because the heavy symbols are re-exported below.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from champ_assistant.bootstrap_installer import _bootstrap_install  # noqa: F401
from champ_assistant.cli import (  # noqa: F401
    DEFAULT_DATA_DIR,
    DEFAULT_FIXTURE_DIR,
    build_parser,
)
from champ_assistant.logging_setup import install_tag_filter, make_formatter
from champ_assistant.runtime_factory import (  # noqa: F401
    _STARTER_CHAMPIONS,
    _build_assistant,
    _build_profile_service,
    _make_source,
    _starter_champion_index,
)
# Re-export every helper that pre-split call sites used to reach for via
# ``from champ_assistant.__main__ import …``. These all live in ``boot``
# now; the re-export keeps the public surface unchanged.
from champ_assistant.boot import (  # noqa: F401
    _check_and_notify_update,
    _enable_gpu_backend,
    _hydrate_champions_and_icons,
    _load_dotenv_files,
    _log_directory,
    _log_startup_summary,
    _resource_root,
    _run_headless,
    _run_lcda_watcher,
    _run_update,
    _run_with_ui,
    _safe_start,
    _setup_file_logger,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Bootstrap-install path: launched by the previous version's apply_update
    # to install ourselves. Handled before any heavy imports (no Qt loaded).
    if args.bootstrap_install:
        staged = Path(args.bootstrap_staged) if args.bootstrap_staged else Path(sys.executable).resolve().parent
        return _bootstrap_install(
            staged,
            Path(args.bootstrap_install),
            parent_pid=args.bootstrap_parent_pid or 0,
        )

    _load_dotenv_files()

    # Install tagged formatter on the default stderr handler. We can't
    # rely on logging.basicConfig's `format=` because the stamped
    # ``subsystem`` field comes from a per-handler Filter, not the
    # formatter alone — so we configure the root + stderr handler
    # explicitly here.
    root = logging.getLogger()
    root.setLevel(args.log_level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stderr = logging.StreamHandler()
        stderr.setFormatter(make_formatter())
        install_tag_filter(stderr)
        root.addHandler(stderr)
    else:
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler):
                h.setFormatter(make_formatter())
                install_tag_filter(h)
    log_file = _setup_file_logger()
    logging.getLogger(__name__).info("startup log_file=%s args=%r", log_file, vars(args))

    if not args.dry_run and not args.no_ui:
        print("[champ-assistant] live mode runs the same way as --dry-run, "
              "minus the fixture replay. Phase 7 wires DataDragon for champion names.",
              file=sys.stderr)

    if args.no_ui:
        # Headless still constructs MainOverlay (orchestrator wiring is shared),
        # and any QWidget requires a live QApplication. PyQt6 doesn't keep it
        # alive on its own — the Python local reference does — so we MUST bind
        # it here for the duration of asyncio.run().
        qt_app = QApplication.instance() or QApplication(sys.argv[:1])
        try:
            return asyncio.run(_run_headless(args))
        except KeyboardInterrupt:
            return 0
        finally:
            del qt_app

    return _run_with_ui(args)


if __name__ == "__main__":
    sys.exit(main())

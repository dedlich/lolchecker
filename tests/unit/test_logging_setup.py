"""Tests for the subsystem-tag logging filter."""
from __future__ import annotations

import io
import logging

from champ_assistant.logging_setup import (
    SubsystemTagFilter,
    _tag_for,
    install_tag_filter,
    make_formatter,
)


def test_tag_for_known_subsystems() -> None:
    assert _tag_for("champ_assistant.hotkey_service") == "HOTKEY"
    assert _tag_for("champ_assistant.state_store") == "STATE"
    assert _tag_for("champ_assistant.render_scheduler") == "RENDER"
    assert _tag_for("champ_assistant.update_check") == "UPDATE"
    assert _tag_for("champ_assistant.layout") == "LAYOUT"
    assert _tag_for("champ_assistant.window_flags") == "WINDOW"
    assert _tag_for("champ_assistant.lifecycle") == "LIFECYC"


def test_tag_for_subpackage_inherits_prefix() -> None:
    assert _tag_for("champ_assistant.ui.overlay") == "UI"
    assert _tag_for("champ_assistant.lcu.client") == "LCU"
    assert _tag_for("champ_assistant.data.loader") == "DATA"


def test_tag_for_unknown_app_module_falls_back_to_app() -> None:
    assert _tag_for("champ_assistant.something_new") == "APP"


def test_tag_for_external_logger() -> None:
    assert _tag_for("httpx") == "EXT"


def test_filter_stamps_record_with_subsystem() -> None:
    f = SubsystemTagFilter()
    record = logging.LogRecord(
        name="champ_assistant.hotkey_service",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="hello",
        args=None,
        exc_info=None,
    )
    f.filter(record)
    assert record.subsystem == "HOTKEY"


def test_full_pipeline_renders_tag_in_formatted_output() -> None:
    """End-to-end: configure a fresh handler, log through a child logger,
    verify the captured output carries the bracketed tag."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(make_formatter())
    install_tag_filter(handler)

    logger = logging.getLogger("champ_assistant.layout")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # isolate from other test handlers

    try:
        logger.info("test message")
    finally:
        logger.removeHandler(handler)
        logger.propagate = True

    out = buf.getvalue()
    assert "[LAYOUT" in out  # padded to width 7
    assert "test message" in out


def test_install_tag_filter_is_idempotent() -> None:
    handler = logging.StreamHandler()
    install_tag_filter(handler)
    install_tag_filter(handler)
    install_tag_filter(handler)
    tag_filters = [f for f in handler.filters if isinstance(f, SubsystemTagFilter)]
    assert len(tag_filters) == 1

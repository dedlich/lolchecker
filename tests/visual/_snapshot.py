"""Deterministic widget-tree snapshot helper for visual regression tests.

Why structural snapshots, not pixel snapshots
=============================================
Pixel snapshots in PyQt6 are not cross-platform stable: ``SF Mono`` /
``Consolas`` / ``Menlo`` render the same string with subtly different
metrics, ``AA_UseDesktopOpenGL`` differs from the offscreen platform,
and Retina vs. non-Retina shifts the antialiasing grid. The same widget
on a CI Linux box vs. a macOS dev machine vs. a Windows install can
diff at 5–15 % pixel-wise — well above any tolerance that would still
catch a real layout regression.

What this module captures
-------------------------
For every widget in the tree, deterministic Python-side properties
that *can* drift in a refactor without anyone noticing:

  * class + objectName + visible state
  * stylesheet string (the design tokens, post-interpolation)
  * the active layout's type, spacing, and contentsMargins
  * widget text (for QLabel / QPushButton / QToolButton-likes — text
    drift is the #1 case "text says 5:00 today, the next refactor
    accidentally renders 0:5:00" we want to catch)
  * children, walked in QObject creation order (which Qt guarantees
    deterministic for ``findChildren``)

What this module does NOT capture
---------------------------------
Absolute pixel x/y/w/h for content-driven widgets (e.g. a QLabel
containing "5:00" measures slightly wider on macOS than Linux because
the font metrics differ). Including those would re-introduce the
cross-platform problem we picked structural snapshots to avoid. Layout
parameters (margins, spacing, fixed sizes set explicitly via
setFixedSize) ARE stable cross-platform and ARE captured.
"""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

UPDATE_ENV = "UPDATE_VISUAL_BASELINES"


def _layout_info(widget: QWidget) -> dict[str, Any] | None:
    layout = widget.layout()
    if layout is None:
        return None
    m = layout.contentsMargins()
    return {
        "type": type(layout).__name__,
        "spacing": layout.spacing(),
        "margins": [m.left(), m.top(), m.right(), m.bottom()],
    }


def _maybe_text(widget: QWidget) -> str | None:
    """Capture .text() for widgets that have it, but only if the value
    is short enough to be a label / button / cell — long rich-text
    blocks (e.g. settings_dialog help paragraphs with embedded URLs)
    are noise in the snapshot diff."""
    if not hasattr(widget, "text"):
        return None
    try:
        text = widget.text()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — must not break snapshotting
        return None
    if not isinstance(text, str):
        return None
    if len(text) > 120:
        # Truncate with a marker so a refactor that swapped the help
        # text still surfaces *something* in the diff without dumping
        # a paragraph.
        return text[:120] + "…"
    return text


def _fixed_size_or_none(widget: QWidget) -> list[int] | None:
    """Return [w, h] iff the widget has an explicit fixed size; otherwise
    None. We don't snapshot natural / content-driven sizes because those
    depend on font metrics and are not cross-platform stable."""
    minimum = widget.minimumSize()
    maximum = widget.maximumSize()
    if (
        minimum.width() == maximum.width()
        and minimum.height() == maximum.height()
        and minimum.width() > 0
        and minimum.height() > 0
    ):
        return [minimum.width(), minimum.height()]
    return None


def snapshot_widget(widget: QWidget) -> dict[str, Any]:
    """Recursive deterministic snapshot of ``widget`` + its tree.

    Children are walked via ``findChildren`` with FindDirectChildrenOnly,
    which preserves Qt-object creation order — that's deterministic for
    code that builds widgets in a fixed sequence (which our UI does).
    """
    children = [
        snapshot_widget(child)
        for child in widget.findChildren(
            QWidget,
            options=Qt.FindChildOption.FindDirectChildrenOnly,
        )
    ]
    snap: dict[str, Any] = {
        "class": type(widget).__name__,
        "objectName": widget.objectName() or "",
        "visible": widget.isVisibleTo(widget.parentWidget()) if widget.parentWidget() else widget.isVisible(),
        "stylesheet": widget.styleSheet(),
        "layout": _layout_info(widget),
        "fixed_size": _fixed_size_or_none(widget),
    }
    text = _maybe_text(widget)
    if text is not None:
        snap["text"] = text
    snap["children"] = children
    return snap


# --------------------------------------------------------------------------
# Baseline storage + diff
# --------------------------------------------------------------------------
def baseline_path(name: str) -> Path:
    return Path(__file__).parent / "baseline" / f"{name}.json"


def assert_snapshot_matches(name: str, snapshot: dict[str, Any]) -> None:
    """Compare ``snapshot`` against the on-disk baseline for ``name``.

    Behavior:
      * Baseline missing OR ``UPDATE_VISUAL_BASELINES=1``: write the
        current snapshot and pass. The first run after a UI change
        creates the new baseline; the next run validates against it.
      * Otherwise: assert byte-equal. On mismatch, raise with a unified
        diff so pytest's failure output points at the exact key path.
    """
    update = bool(os.environ.get(UPDATE_ENV))
    path = baseline_path(name)
    actual = json.dumps(snapshot, indent=2, sort_keys=True)

    if update or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual + "\n", encoding="utf-8")
        if update:
            return
        # Fresh baseline created — pass once so the run isn't a failure
        # the first time, but log so the developer knows the file is new.
        import warnings
        warnings.warn(
            f"visual baseline created at {path.relative_to(path.parents[2])}; "
            f"commit it to lock the snapshot.",
            stacklevel=2,
        )
        return

    expected = path.read_text(encoding="utf-8").rstrip("\n")
    if actual == expected:
        return

    diff = "\n".join(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile=f"baseline/{name}.json",
            tofile="current",
            lineterm="",
        )
    )
    raise AssertionError(
        f"visual regression in '{name}':\n{diff}\n\n"
        f"If this change is intentional, regenerate the baseline with:\n"
        f"    {UPDATE_ENV}=1 .venv/bin/python -m pytest tests/visual/\n"
        f"and commit the updated {path.relative_to(path.parents[2])}."
    )

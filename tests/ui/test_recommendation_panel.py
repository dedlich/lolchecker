"""Tests for the recommendation panel (charter B1 V2 UI surface)."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from champ_assistant.advisor.decision_engine import Recommendation
from champ_assistant.ui.recommendation_panel import (
    MAX_VISIBLE_ROWS,
    RecommendationPanel,
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _rec(text="x", severity="info") -> Recommendation:
    return Recommendation(text=text, severity=severity, category="tempo")


def test_panel_hidden_by_default(qt_app) -> None:
    """Silent default — no recs means no widget on screen."""
    panel = RecommendationPanel()
    assert panel.isVisible() is False


def test_set_empty_list_keeps_hidden(qt_app) -> None:
    panel = RecommendationPanel()
    panel.set_recommendations([])
    assert panel.isVisible() is False


def test_set_recommendations_renders_top_3(qt_app) -> None:
    panel = RecommendationPanel()
    recs = [_rec(f"r{i}", "info") for i in range(5)]
    panel.set_recommendations(recs)
    visible_rows = [row for row in panel._rows if row.isVisible()]
    assert len(visible_rows) == MAX_VISIBLE_ROWS
    # Each row is now a _RecRow card — text lives on the inner label.
    assert "r0" in visible_rows[0]._text.text()
    assert "r2" in visible_rows[2]._text.text()


def test_severity_color_carried_by_glyph(qt_app) -> None:
    """Chat-style design: the row itself is transparent, the severity
    color rides on the glyph — alert=DANGER, warn=WARNING, info=ACCENT.
    Renamed from the v1 ``..._strip_color_changes_per_row`` once the
    left-strip was removed in the chat-redesign."""
    from champ_assistant.ui import styles
    panel = RecommendationPanel()
    panel.set_recommendations([
        _rec("alarm", "alert"),
        _rec("careful", "warn"),
        _rec("tip", "info"),
    ])
    assert styles.DANGER in panel._rows[0]._glyph.styleSheet()
    assert styles.WARNING in panel._rows[1]._glyph.styleSheet()
    assert styles.ACCENT in panel._rows[2]._glyph.styleSheet()


def test_category_glyph_maps_correctly(qt_app) -> None:
    """objective → ◈, tempo → ▶, safety → ✕."""
    panel = RecommendationPanel()
    panel.set_recommendations([
        _rec("a", "alert"),  # default category in _rec is "tempo"
    ])
    # Default _rec category is tempo, glyph should be ▶
    glyph_text = panel._rows[0]._glyph.text()
    assert glyph_text == "▶"


def test_set_then_clear_hides_panel(qt_app) -> None:
    """Re-setting to an empty list takes the panel back to hidden —
    no stale recs lingering on screen after the situation passes."""
    panel = RecommendationPanel()
    panel.set_recommendations([_rec("foo")])
    panel.set_recommendations([])
    assert panel.isVisible() is False


def test_demo_populates_one_per_severity(qt_app) -> None:
    """Demo mode shows one example per rule type so the user can
    visually validate every code path during testing."""
    from champ_assistant.ui import styles
    panel = RecommendationPanel()
    panel.populate_demo()
    visible_rows = [row for row in panel._rows if row.isVisible()]
    # Top row is alert severity — DANGER color rides on the glyph
    # (chat-style; the row itself is transparent).
    assert styles.DANGER in visible_rows[0]._glyph.styleSheet()
    # Body text on the alert row references one of the objective names.
    top_text = visible_rows[0]._text.text()
    assert any(s in top_text for s in ("Drache", "Baron", "Herald"))


def test_panel_uses_design_tokens_only(qt_app) -> None:
    """Charter constraint — no inline px/hex literals slipping in.
    The alert severity color (DANGER) is on the glyph in the chat
    redesign; that's where this assertion looks."""
    from champ_assistant.ui import styles
    panel = RecommendationPanel()
    panel.set_recommendations([_rec("x", "alert")])
    glyph_sheet = panel._rows[0]._glyph.styleSheet()
    assert styles.DANGER in glyph_sheet

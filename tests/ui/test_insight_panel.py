"""Tests for the InsightPanel detail view (v2 spec)."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QLabel

from champ_assistant.advisor.decision_engine import Recommendation
from champ_assistant.ui.insight_panel import InsightPanel


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _rec(
    text="Force Drake", severity="alert", category="objective",
    confidence=0.85, risk="MEDIUM",
    reasons=("Drache spawnt in 25s", "Team-Gold-Diff: +1500"),
) -> Recommendation:
    return Recommendation(
        text=text, severity=severity, category=category,
        confidence=confidence, risk=risk,
        reasons=tuple(reasons),
    )


def test_panel_hidden_by_default(qt_app) -> None:
    panel = InsightPanel()
    assert panel.isVisible() is False


def test_set_recommendation_renders_title(qt_app) -> None:
    panel = InsightPanel()
    panel.set_recommendation(_rec(text="Force Drake"))
    assert panel._title.text() == "Force Drake"


def test_set_recommendation_renders_meta_pills(qt_app) -> None:
    panel = InsightPanel()
    panel.set_recommendation(_rec(confidence=0.78, risk="HIGH"))
    assert "78%" in panel._confidence.text()
    assert "HIGH" in panel._risk.text()


def test_set_recommendation_renders_reasons(qt_app) -> None:
    panel = InsightPanel()
    reasons = ("Reason 1", "Reason 2", "Reason 3")
    panel.set_recommendation(_rec(reasons=reasons))
    rendered = [
        w.text()
        for w in panel.findChildren(QLabel)
        if w.text().startswith("•")
    ]
    assert len(rendered) == 3
    assert "Reason 1" in rendered[0]


def test_no_reasons_shows_fallback_label(qt_app) -> None:
    """Empty reasons → italic 'no detail available' message rather
    than an empty bullet list."""
    panel = InsightPanel()
    panel.set_recommendation(_rec(reasons=()))
    fallback_visible = any(
        "verfügbar" in w.text()
        for w in panel.findChildren(QLabel)
    )
    assert fallback_visible


def test_none_recommendation_resets_to_empty_state(qt_app) -> None:
    panel = InsightPanel()
    panel.set_recommendation(_rec())
    panel.set_recommendation(None)
    assert "Keine" in panel._title.text()


def test_confidence_pill_color_matches_band(qt_app) -> None:
    """≥0.8 → success green, ≥0.5 → accent, <0.5 → muted."""
    from champ_assistant.ui import styles
    panel = InsightPanel()
    panel.set_recommendation(_rec(confidence=0.9))
    assert styles.SUCCESS in panel._confidence.styleSheet()
    panel.set_recommendation(_rec(confidence=0.6))
    assert styles.ACCENT in panel._confidence.styleSheet()


def test_risk_pill_color_matches_level(qt_app) -> None:
    from champ_assistant.ui import styles
    panel = InsightPanel()
    panel.set_recommendation(_rec(risk="HIGH"))
    assert styles.DANGER in panel._risk.styleSheet()
    panel.set_recommendation(_rec(risk="LOW"))
    assert styles.SUCCESS in panel._risk.styleSheet()


def test_toggle_shows_then_hides(qt_app) -> None:
    panel = InsightPanel()
    panel.toggle(_rec())
    assert panel.isVisible()
    panel.toggle(_rec())
    assert not panel.isVisible()

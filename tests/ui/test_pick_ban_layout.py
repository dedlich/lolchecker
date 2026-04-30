"""Tests for the reworked PickCard + BanRow layouts.

Coverage focus:
  * Rank prefix shown when provided, omitted when not (back-compat)
  * Score uses muted/secondary color, not the previous heavy
    accent/danger primary treatment
  * Champion name remains the primary visual element
  * Multiple suggestions render with sequential rank prefixes
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QLabel

from champ_assistant.advisor.ban_suggestions import BanSuggestion
from champ_assistant.advisor.picks import PickSuggestion
from champ_assistant.ui import styles
from champ_assistant.ui.ban_panel import BanPanel, _BanRow
from champ_assistant.ui.pick_card import PickCard


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def test_pick_card_renders_item_icons_when_available(qt_app) -> None:
    """When item_icons map carries pixmaps for the build's items,
    PickCard renders them as inline swatches instead of plain text."""
    from PyQt6.QtGui import QImage, QPixmap
    from champ_assistant.data.models import ChampionBuild
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(0xFFFFFF)
    pix = QPixmap.fromImage(img)
    icons = {"Stridebreaker": pix, "Plated Steelcaps": pix}
    build = ChampionBuild(
        runes=["Conqueror"],
        items=["Stridebreaker", "Plated Steelcaps"],
        summoners=["Flash"],
    )
    card = PickCard(_suggestion("Garen"), build=build, item_icons=icons, rank=1)
    # Find QLabels with non-null pixmaps (the rune/summoner lines use
    # text-only QLabels; only items get pixmap-backed swatches).
    labels = card.findChildren(QLabel)
    pixmap_labels = [
        l for l in labels
        if l.pixmap() is not None and not l.pixmap().isNull()
    ]
    assert len(pixmap_labels) == 2  # two items, two swatches


def test_pick_card_falls_back_to_text_when_no_item_icon(qt_app) -> None:
    """A build whose item names aren't in the icon map should still
    render — as text labels, not as broken/empty pixmaps."""
    from champ_assistant.data.models import ChampionBuild
    build = ChampionBuild(items=["MysteryItem"])
    card = PickCard(_suggestion("Garen"), build=build, item_icons={}, rank=1)
    labels = card.findChildren(QLabel)
    text_labels = [l for l in labels if "MysteryItem" in l.text()]
    assert len(text_labels) >= 1


def _suggestion(name: str = "Ahri", score: float = 84.5) -> PickSuggestion:
    return PickSuggestion(
        champion_key=name, score=score, tier="A",
        reasons=["counters Yasuo", "good vs assassins"],
    )


def _ban(name: str = "Yone", score: float = 92.0) -> BanSuggestion:
    return BanSuggestion(
        champion_key=name, score=score,
        reasons=["meta threat"],
    )


# ----------------------------------------------------------------------
# PickCard — rank prefix + score demotion
# ----------------------------------------------------------------------
def test_pick_card_shows_rank_prefix_when_provided(qt_app) -> None:  # type: ignore[no-untyped-def]
    card = PickCard(_suggestion(), rank=1)
    rank_labels = [
        w for w in card.findChildren(QLabel)
        if w.text() == "#1"
    ]
    assert len(rank_labels) == 1


def test_pick_card_omits_rank_when_not_provided(qt_app) -> None:  # type: ignore[no-untyped-def]
    """Backward compat: omitting rank means no badge appears."""
    card = PickCard(_suggestion())
    rank_labels = [
        w for w in card.findChildren(QLabel)
        if w.text().startswith("#")
    ]
    assert rank_labels == []


def test_pick_card_score_uses_secondary_label_format(qt_app) -> None:  # type: ignore[no-untyped-def]
    """Score is now prefixed 'score N' in muted color, no longer
    a primary accent number competing with the champion name."""
    card = PickCard(_suggestion(score=84.0), rank=1)
    score_labels = [
        w for w in card.findChildren(QLabel)
        if w.text() == "score 84"
    ]
    assert len(score_labels) == 1
    assert styles.TEXT_MUTED in score_labels[0].styleSheet()
    # Score should NOT use the accent color anymore.
    assert styles.ACCENT not in score_labels[0].styleSheet()


def test_pick_card_name_uses_primary_color(qt_app) -> None:  # type: ignore[no-untyped-def]
    card = PickCard(_suggestion(name="Ahri"))
    name_labels = [w for w in card.findChildren(QLabel) if w.text() == "Ahri"]
    assert len(name_labels) == 1
    assert styles.TEXT_PRIMARY in name_labels[0].styleSheet()


def test_pick_card_renders_reasons(qt_app) -> None:  # type: ignore[no-untyped-def]
    card = PickCard(_suggestion())
    reason_labels = [
        w for w in card.findChildren(QLabel)
        if "counters Yasuo" in w.text()
    ]
    assert len(reason_labels) == 1


# ----------------------------------------------------------------------
# BanRow — rank prefix + no pill
# ----------------------------------------------------------------------
def test_ban_row_shows_rank_prefix_when_provided(qt_app) -> None:  # type: ignore[no-untyped-def]
    row = _BanRow(_ban(), icon=None, rank=1)
    rank_labels = [w for w in row.findChildren(QLabel) if w.text() == "#1"]
    assert len(rank_labels) == 1


def test_ban_row_omits_rank_when_not_provided(qt_app) -> None:  # type: ignore[no-untyped-def]
    row = _BanRow(_ban(), icon=None)
    rank_labels = [w for w in row.findChildren(QLabel) if w.text().startswith("#")]
    assert rank_labels == []


def test_ban_row_score_no_longer_has_pill_background(qt_app) -> None:  # type: ignore[no-untyped-def]
    """The previous design rendered the score with a red rgba
    background + border pill. Audit: that styling is gone — score
    is plain text, no background-color in its stylesheet."""
    row = _BanRow(_ban(score=92.0), icon=None)
    score_labels = [w for w in row.findChildren(QLabel) if w.text() == "92"]
    assert len(score_labels) == 1
    css = score_labels[0].styleSheet()
    assert "background" not in css.lower(), (
        f"score still has pill background: {css}"
    )


def test_ban_row_score_uses_danger_color(qt_app) -> None:  # type: ignore[no-untyped-def]
    """Danger color survives — the ban semantic stays via color +
    left-border, just without the heavy pill chrome."""
    row = _BanRow(_ban(score=92.0), icon=None)
    score_labels = [w for w in row.findChildren(QLabel) if w.text() == "92"]
    assert styles.DANGER in score_labels[0].styleSheet()


# ----------------------------------------------------------------------
# BanPanel — rank gets propagated through update_suggestions
# ----------------------------------------------------------------------
def test_ban_panel_assigns_sequential_ranks(qt_app) -> None:  # type: ignore[no-untyped-def]
    panel = BanPanel()
    panel.update_suggestions(
        [_ban("Yone", 92), _ban("Akali", 88), _ban("Yasuo", 85)],
        icon_lookup=lambda _: None,
    )
    rows = panel.findChildren(_BanRow)
    assert len(rows) == 3

    # Each row should have a sequential rank label.
    for expected_rank, row in enumerate(rows, start=1):
        rank_labels = [
            w for w in row.findChildren(QLabel) if w.text() == f"#{expected_rank}"
        ]
        assert len(rank_labels) == 1, (
            f"row {expected_rank} missing rank label"
        )


def test_ban_panel_empty_list_hides(qt_app) -> None:  # type: ignore[no-untyped-def]
    panel = BanPanel()
    panel.update_suggestions([_ban()], icon_lookup=lambda _: None)
    assert panel.isVisible() is True or panel.isHidden()  # constructed-then-shown
    panel.update_suggestions([], icon_lookup=lambda _: None)
    assert panel.isHidden()

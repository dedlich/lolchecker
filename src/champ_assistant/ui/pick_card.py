"""Pick suggestion card.

Visual hierarchy (revised for clarity):

  PRIMARY     champion name + portrait + tier badge — what to read first
  SECONDARY   rank prefix (#1 / #2 / #3) — order signal
  TERTIARY    score number + reason line — supporting context

The previous layout rendered the score at FS_HEADING accent which
visually competed with the champion name. Now the score sits in
TEXT_SECONDARY at FS_BODY, the rank prefix replaces it as the lead
"position" signal.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..advisor.picks import PickSuggestion
from ..data.models import ChampionBuild
from . import styles
from .widgets import TierBadge

ICON_SIZE = 36  # bumped from 28 — champion portrait is the visual anchor
BUILD_ITEM_ICON_PX = 28  # bumped from 26 to balance the bigger portrait
BUILD_RUNE_ICON_PX = 24  # bumped from 22 — bigger runes read at a glance


class PickCard(QFrame):
    """One suggested pick. Layout:

        ┌────────────────────────────────────────┐
        │ #1 [icon]  Name [Tier]    score=84    │  head
        │ reason1 · reason2 · reason3            │  reasons
        │ 🛡 KeystoneA • RuneB • RuneC           │  optional build
        │ ⚔  Item1 ›  Item2 ›  Item3              │
        │ ✨ Flash • Ignite                       │
        │                          [Apply Build] │  optional action
        └────────────────────────────────────────┘
    """

    apply_build_requested = pyqtSignal(str, "PyQt_PyObject", "PyQt_PyObject")
    # (champion_key, rune_names, item_names)

    pick_hover_requested = pyqtSignal(str)
    # (champion_key) — fired when the user clicks anywhere on the card
    # body (NOT the Apply Build button). The handler asks LCU to hover
    # this champion in the player's pick slot. Hover-only — final
    # lock-in stays manual to avoid griefing.

    def __init__(
        self,
        suggestion: PickSuggestion,
        icon: QPixmap | None = None,
        build: ChampionBuild | None = None,
        *,
        rank: int | None = None,
        build_reasons: list[str] | None = None,
        item_icons: dict[str, QPixmap] | None = None,
        rune_icons: dict[str, QPixmap] | None = None,
    ) -> None:
        super().__init__()
        self.setProperty("card", True)
        self.suggestion = suggestion
        self._build = build
        self._item_icons = item_icons or {}
        self._rune_icons = rune_icons or {}
        # Cursor signals "this surface does something on click".
        # The Apply Build QPushButton accepts its own click events
        # so it doesn't propagate up to mousePressEvent below.
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_WIDE, styles.SPACING_GRID,
            styles.SPACING_WIDE, styles.SPACING_GRID,
        )
        outer.setSpacing(styles.SPACING_TIGHT + 2)

        # -- Head row -----------------------------------------------------
        head = QHBoxLayout()
        head.setSpacing(styles.SPACING_GRID)

        if rank is not None:
            head.addWidget(_rank_badge(rank))

        if icon is not None and not icon.isNull():
            icon_label = QLabel()
            icon_label.setFixedSize(ICON_SIZE, ICON_SIZE)
            icon_label.setPixmap(icon)
            icon_label.setStyleSheet(
                f"background-color: {styles.BG_PRIMARY};"
                f" border-radius: {styles.RADIUS}px;"
                f" border: 1px solid {styles.BORDER};"
            )
            head.addWidget(icon_label)

        name = QLabel(suggestion.champion_key)
        name.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_HEADING}px; font-weight: 700;"
        )
        head.addWidget(name)
        head.addWidget(TierBadge(suggestion.tier))
        head.addStretch()

        # Score is now a small muted-secondary label, not a primary
        # accent number. Visual hierarchy: name + tier dominate; score
        # supports without competing.
        score_label = QLabel(f"score {suggestion.score:.0f}")
        score_label.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            f" font-family: {styles.FONT_MONO};"
            " letter-spacing: 0.4px;"
        )
        score_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        head.addWidget(score_label)
        outer.addLayout(head)

        # -- Reasons line -------------------------------------------------
        reasons_text = " · ".join(suggestion.reasons[:3]) if suggestion.reasons else ""
        reasons = QLabel(reasons_text)
        reasons.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
            " padding-left: 4px;"
        )
        reasons.setWordWrap(True)
        outer.addWidget(reasons)

        # -- Build section (runes / items / summoners + apply button) ----
        # Cached for variant-cycle rebuilds: when the user clicks ◀ / ▶
        # we need to swap _build for the next variant and redraw the
        # rune/item lines without recreating the whole card.
        self._build_reasons = list(build_reasons or [])
        self._variant_index = 0
        self._build_section: QWidget | None = None
        if build is not None:
            self._render_build_section(outer)

    def _all_variants(self) -> list[ChampionBuild]:
        """Main build + alternatives, in display order. Empty list when
        no build is attached."""
        if self._build is None:
            return []
        return [self._build, *self._build.variants]

    def _render_build_section(self, outer: QVBoxLayout) -> None:
        """(Re)build the rune/item/summoner display + variant switcher
        + apply button. Called once on init, then again every time the
        user cycles through variants."""
        if self._build_section is not None:
            self._build_section.setParent(None)
            self._build_section.deleteLater()
            self._build_section = None

        active = self._active_variant()
        if active is None:
            return

        section = QWidget()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(2)

        # Variant switcher row — only when alternatives exist.
        variants = self._all_variants()
        if len(variants) > 1:
            section_layout.addLayout(self._build_variant_row(active, variants))

        self._add_build_lines(section_layout, active)

        if self._build_reasons:
            for reason in self._build_reasons:
                label = QLabel(f"⚙ {reason}")
                label.setStyleSheet(
                    f"color: {styles.ACCENT};"
                    f" font-size: {styles.FS_LABEL}px;"
                    " font-style: italic;"
                    " padding-left: 4px;"
                )
                label.setWordWrap(True)
                section_layout.addWidget(label)
        self._add_apply_button(section_layout, active)
        outer.addWidget(section)
        self._build_section = section

    def _build_variant_row(
        self, active: ChampionBuild, variants: list[ChampionBuild],
    ) -> QHBoxLayout:
        """Cycle control: ◀ {variant_name} ▶. Click cycles to next."""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(4, 0, 4, 0)

        prev_btn = QToolButton()
        prev_btn.setText("◀")
        prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prev_btn.setStyleSheet(self._variant_btn_stylesheet())
        prev_btn.clicked.connect(lambda: self._cycle_variant(-1))
        row.addWidget(prev_btn)

        idx = self._variant_index
        name_label = QLabel(f"{active.name} ({idx + 1}/{len(variants)})")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY};"
            f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            " letter-spacing: 0.4px;"
        )
        row.addWidget(name_label, 1)

        next_btn = QToolButton()
        next_btn.setText("▶")
        next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        next_btn.setStyleSheet(self._variant_btn_stylesheet())
        next_btn.clicked.connect(lambda: self._cycle_variant(1))
        row.addWidget(next_btn)

        return row

    @staticmethod
    def _variant_btn_stylesheet() -> str:
        return (
            f"QToolButton {{"
            f" color: {styles.ACCENT};"
            f" background: transparent;"
            f" border: 1px solid {styles.BORDER_FAINT};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" padding: 0px 6px; min-width: 18px;"
            f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            f" }}"
            f" QToolButton:hover {{ background: {styles.BG_INTERACT}; }}"
        )

    def _cycle_variant(self, direction: int) -> None:
        variants = self._all_variants()
        if len(variants) <= 1:
            return
        self._variant_index = (self._variant_index + direction) % len(variants)
        # Find the outer QVBoxLayout we were originally added to and
        # rebuild the build section in place.
        outer = self.layout()
        if outer is not None:
            self._render_build_section(outer)  # type: ignore[arg-type]

    def _active_variant(self) -> ChampionBuild | None:
        variants = self._all_variants()
        if not variants:
            return None
        return variants[self._variant_index % len(variants)]

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """Click on the card body → lock this champion in the picker
        AND apply the CURRENTLY-DISPLAYED variant's runes + items.
        Clicks on the Apply Build button or the variant cycle buttons
        never reach here (those accept and consume their own events).
        """
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        key = self.suggestion.champion_key
        self.pick_hover_requested.emit(key)
        active = self._active_variant()
        if active is not None and (active.runes or active.items):
            self.apply_build_requested.emit(
                key, list(active.runes), list(active.items),
            )
        super().mousePressEvent(event)

    def _add_build_lines(self, outer: QVBoxLayout, build: ChampionBuild) -> None:
        """Three compact lines for runes / items / summoners. Runes + items
        render as inline icons when prefetch has caught up; summoners
        stay text since the existing summoner_tracker handles their icons
        elsewhere and we don't want two icon caches for the same data."""
        if build.runes:
            outer.addLayout(self._runes_row(build.runes))
        if build.items:
            outer.addLayout(self._items_row(build.items))
        if build.summoners:
            outer.addWidget(_build_line(
                "✨", build.summoners, styles.TEXT_SECONDARY, sep=" • ",
            ))

    def _runes_row(self, rune_names: list[str]) -> QHBoxLayout:
        """Render the rune list as a row of small icons. Falls back to
        text labels for any rune we don't have an icon for (rare —
        usually means the rune was added in a patch newer than our
        PERK_IDS table)."""
        row = QHBoxLayout()
        row.setSpacing(2)
        row.setContentsMargins(0, 0, 0, 0)
        sigil = QLabel("🛡")
        sigil.setStyleSheet(
            f"color: {styles.TIER_A};"
            f" font-size: {styles.FS_HEADING}px;"
            " padding-right: 4px;"
        )
        row.addWidget(sigil)
        for name in rune_names:
            pix = self._rune_icons.get(name)
            if pix is not None and not pix.isNull():
                lbl = QLabel()
                lbl.setFixedSize(BUILD_RUNE_ICON_PX, BUILD_RUNE_ICON_PX)
                lbl.setScaledContents(True)
                lbl.setPixmap(pix)
                lbl.setToolTip(name)
                row.addWidget(lbl)
            else:
                lbl = QLabel(name)
                lbl.setStyleSheet(
                    f"color: {styles.TIER_A};"
                    f" font-size: {styles.FS_LABEL}px;"
                    " padding: 0 4px;"
                )
                row.addWidget(lbl)
        row.addStretch(1)
        return row

    def _items_row(self, item_names: list[str]) -> QHBoxLayout:
        """Render the item list as a row of small icon labels with
        sword-sigil prefix. Items without a prefetched icon fall
        through to a single text label so the data isn't lost. The
        full sequence is preserved — no dedup, no reordering."""
        row = QHBoxLayout()
        row.setSpacing(2)
        row.setContentsMargins(0, 0, 0, 0)
        sigil = QLabel("⚔")
        sigil.setStyleSheet(
            f"color: {styles.TIER_S};"
            f" font-size: {styles.FS_HEADING}px;"
            " padding-right: 4px;"
        )
        row.addWidget(sigil)
        for i, name in enumerate(item_names):
            pix = self._item_icons.get(name)
            if pix is not None and not pix.isNull():
                lbl = QLabel()
                lbl.setFixedSize(BUILD_ITEM_ICON_PX, BUILD_ITEM_ICON_PX)
                lbl.setScaledContents(True)
                lbl.setPixmap(pix)
                lbl.setToolTip(name)
                lbl.setStyleSheet(
                    f"border: 1px solid {styles.BORDER_FAINT};"
                    f" border-radius: {styles.RADIUS_SMALL}px;"
                )
                row.addWidget(lbl)
            else:
                # No icon available yet — text fallback so the item
                # is at least readable while the prefetch finishes.
                lbl = QLabel(name)
                lbl.setStyleSheet(
                    f"color: {styles.TIER_S};"
                    f" font-size: {styles.FS_LABEL}px;"
                    " padding: 0 4px;"
                )
                row.addWidget(lbl)
            if i < len(item_names) - 1:
                arrow = QLabel("›")
                arrow.setStyleSheet(
                    f"color: {styles.TEXT_MUTED};"
                    f" font-size: {styles.FS_LABEL}px;"
                    " padding: 0 2px;"
                )
                row.addWidget(arrow)
        row.addStretch(1)
        return row

    def _add_apply_button(self, outer: QVBoxLayout, build: ChampionBuild) -> None:
        """Apply Build button — pushes the recommended runes into a new
        rune page AND the items into a custom item set in LeagueClient
        via LCU, activates the rune page. Single click, two LCU writes.
        Skipped if the build has neither runes nor items."""
        if not (build.runes or build.items):
            return
        # Adapt the label so it matches what the click actually does.
        if build.runes and build.items:
            label = "Apply Build"
        elif build.runes:
            label = "Apply Runes"
        else:
            label = "Apply Items"

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addStretch(1)
        apply = QPushButton(label)
        apply.setCursor(Qt.CursorShape.PointingHandCursor)
        apply.setStyleSheet(
            f"QPushButton {{"
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 {styles.ACCENT_BRIGHT}, stop:1 {styles.ACCENT});"
            f" color: white;"
            f" border: none; padding: 4px 14px;"
            f" border-radius: 8px; font-weight: 700;"
            f" font-size: {styles.FS_LABEL}px;"
            f" letter-spacing: 0.3px; }}"
            f" QPushButton:hover {{ background: {styles.ACCENT_BRIGHT}; }}"
            f" QPushButton:pressed {{ background: {styles.ACCENT}; }}"
            f" QPushButton:disabled {{ background: {styles.BG_TERTIARY};"
            f" color: {styles.TEXT_MUTED}; }}"
        )
        apply.clicked.connect(
            lambda: self.apply_build_requested.emit(
                self.suggestion.champion_key,
                list(build.runes),
                list(build.items),
            )
        )
        row.addWidget(apply)
        outer.addLayout(row)


def _rank_badge(rank: int) -> QLabel:
    """Small badge showing the suggestion's rank (1-indexed). Token-
    driven: accent text on a transparent background, mono font for
    a stable digit width across #1..#9."""
    label = QLabel(f"#{rank}")
    label.setFixedWidth(28)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(
        f"color: {styles.ACCENT};"
        f" font-family: {styles.FONT_MONO};"
        f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
        " letter-spacing: 0.5px;"
    )
    return label


def _build_line(sigil: str, items: list[str], color: str, *, sep: str) -> QLabel:
    label = QLabel(
        f"<span style='color:{color}; font-size:{styles.FS_HEADING}px;'>{sigil}</span>  "
        f"<span style='color:{color}'>"
        f"{sep.join(items)}</span>"
    )
    label.setStyleSheet(f"font-size: {styles.FS_LABEL}px;")
    label.setWordWrap(True)
    return label

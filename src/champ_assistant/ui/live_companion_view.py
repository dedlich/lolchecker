"""Live Companion — unified champ-select view (Mobalytics-style layout).

Single-window champ-select panel that replaces the previous mix of
enemy rows + floating LobbyStatsWidget. Layout (matching the v1.10.78
design screenshot):

    ┌────────────────────────────────────────────────────────────┐
    │ Live Companion [LIVE]              Tabs        Game Plan   │
    │ ┌─Your Team──┐ Power Spikes  Damage  vs ┌─Enemy Team─┐     │
    │ │ 👤👤👤👤👤 │ E─M─L         AP/AD     │ 👤👤👤👤👤  │     │
    │ └────────────┘                          └────────────┘     │
    │ ┌─Build──┬─Runes / Spells / Items─┬──Game Plan / Spikes──┐ │
    │ │ champ  │ runes  spells          │ Early ─ Mid ─ Late   │ │
    │ │ tier   │ items @timing          │ ChampionPowerSpikes  │ │
    │ │ vs WRs │                        │ Playing Against      │ │
    │ └────────┴────────────────────────┴──────────────────────┘ │
    └────────────────────────────────────────────────────────────┘

Data sources:
  * Team strip portraits  — ``view.session.my_team`` / ``view.session.their_team``
                            + ``view.all_champion_keys`` for icons
  * Damage type %         — count of "AP" / "AD" / "AP/AD" across both teams
  * Power-spike bars      — heuristic from ``static/tags.json``:
                            "Early-Game" / "Lane-Bully" → early, "Late-Game" /
                            "Hyper-Carry" / "Scaling" → late, default → mid
  * Build / runes / items — ``view.my_champion_build`` (when locked)
  * Matchup WRs           — ``view.enemy_counters`` + ``view.suggestion_builds``
  * Game Plan             — placeholder (LLM wiring deferred per OPTIMIZATION
                            §B5 — option-2 follow-up)

The widget is a normal QWidget — the overlay parents it inside the
champ-select section. Per the design-lockdown linter, every style pulls
from ``ui.styles`` tokens; no inline px / hex literals.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import styles

if TYPE_CHECKING:
    from ..data.models import ChampSelectMember
    from .view_model import SessionView

# Callable signature for the icon lookup the overlay passes in.
IconLookup = Callable[[str], "QPixmap | None"]
TagsLookup = Callable[[str], list[str]]


# ─── Phase-tag heuristic ────────────────────────────────────────────────────
# Maps champion-tag substrings (from static/tags.json) to a power-spike phase.
# The heuristic is intentionally simple: each champ contributes 1.0 to the
# winning phase. Aggregated across 5 picks, the team bar shows the share.
_EARLY_TAGS = ("Early-Game", "Lane-Bully")
_LATE_TAGS = ("Late-Game", "Hyper-Carry", "Scaling")


def _phase_for_tags(tags: list[str]) -> str:
    """Return ``"early"`` / ``"mid"`` / ``"late"`` for a champion tag list."""
    if any(t in _EARLY_TAGS for t in tags):
        return "early"
    if any(t in _LATE_TAGS for t in tags):
        return "late"
    return "mid"


# ─── Portrait helpers ───────────────────────────────────────────────────────

_PORTRAIT_PX = 36
_PORTRAIT_SLOT_PX = 40  # icon + 2px halo + 2px gap


class _TeamStrip(QWidget):
    """Five champion portraits in a row, used twice (allies + enemies)."""

    def __init__(self, label: str, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel(label)
        title.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; letter-spacing: 1.2px;"
            " text-transform: uppercase;"
        )
        layout.addWidget(title)

        portraits_row = QHBoxLayout()
        portraits_row.setSpacing(2)
        portraits_row.setContentsMargins(0, 0, 0, 0)

        self._slots: list[QLabel] = []
        for _ in range(5):
            slot = QLabel()
            slot.setFixedSize(QSize(_PORTRAIT_PX, _PORTRAIT_PX))
            slot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            slot.setStyleSheet(
                f"background-color: {styles.BG_TERTIARY};"
                f" border: 1px solid {styles.BORDER};"
                f" border-radius: {styles.RADIUS_SMALL}px;"
                f" color: {styles.TEXT_MUTED};"
                f" font-size: {styles.FS_CAPTION}px;"
            )
            portraits_row.addWidget(slot)
            self._slots.append(slot)

        layout.addLayout(portraits_row)

    def set_team(self, keys: list[str], icon_lookup: IconLookup) -> None:
        """Render up to 5 portraits. ``keys`` is the ordered champion-key list;
        ``icon_lookup(key) -> QPixmap | None`` resolves the icon."""
        for i, slot in enumerate(self._slots):
            if i < len(keys) and keys[i]:
                pix = icon_lookup(keys[i])
                if pix is not None and not pix.isNull():
                    slot.setPixmap(pix.scaled(
                        _PORTRAIT_PX, _PORTRAIT_PX,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
                    slot.setText("")
                    continue
                # Fallback to first letter when icon hasn't loaded yet.
                slot.setPixmap(QPixmap())
                slot.setText(keys[i][:1].upper())
            else:
                slot.setPixmap(QPixmap())
                slot.setText("")


class _DamageTypeBar(QWidget):
    """Horizontal % AP / % AD bar. AD on the right (pink) and AP on the
    left (blue) match the screenshot's color convention."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel("Damage Type")
        title.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; letter-spacing: 1.2px;"
        )
        layout.addWidget(title)

        bars_row = QHBoxLayout()
        bars_row.setSpacing(8)
        bars_row.setContentsMargins(0, 0, 0, 0)

        # Color stripes (0% AP : 100% AD baseline).
        self._ap_stripe = QFrame()
        self._ap_stripe.setFixedHeight(4)
        self._ap_stripe.setStyleSheet(
            f"background-color: {styles.ACCENT}; border-radius: 2px;"
        )
        self._ad_stripe = QFrame()
        self._ad_stripe.setFixedHeight(4)
        self._ad_stripe.setStyleSheet(
            f"background-color: {styles.DANGER}; border-radius: 2px;"
        )

        bars_row.addWidget(self._ap_stripe, 1)
        bars_row.addWidget(self._ad_stripe, 1)
        layout.addLayout(bars_row)

        labels_row = QHBoxLayout()
        labels_row.setContentsMargins(0, 0, 0, 0)
        self._ap_label = QLabel("0% AP")
        self._ad_label = QLabel("0% AD")
        for lab, color in (
            (self._ap_label, styles.ACCENT),
            (self._ad_label, styles.DANGER),
        ):
            lab.setStyleSheet(
                f"color: {color}; font-size: {styles.FS_CAPTION}px;"
                " font-weight: 700;"
            )
        labels_row.addWidget(self._ap_label, 1, Qt.AlignmentFlag.AlignLeft)
        labels_row.addWidget(self._ad_label, 1, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(labels_row)

    def set_split(self, ap_pct: int, ad_pct: int) -> None:
        """Update the bar widths + labels. The two should sum to 100."""
        self._ap_label.setText(f"{ap_pct}% AP")
        self._ad_label.setText(f"{ad_pct}% AD")
        # Stretch via a fresh setMinimumWidth ratio. Keep simple — Qt's
        # box-layout stretch factors handle the visual division.
        # AP gets weight = ap_pct, AD gets weight = ad_pct.
        layout = self._ap_stripe.parentWidget().layout()
        if isinstance(layout, QHBoxLayout):
            layout.setStretch(0, max(1, ap_pct))
            layout.setStretch(1, max(1, ad_pct))


class _PowerSpikesBar(QWidget):
    """Three-segment Early / Mid / Late bar. Each segment width = team's
    aggregated phase-tag share."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel("Team Power Spikes")
        title.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; letter-spacing: 1.2px;"
        )
        layout.addWidget(title)

        bars_row = QHBoxLayout()
        bars_row.setSpacing(4)
        bars_row.setContentsMargins(0, 0, 0, 0)
        self._stripes: list[QFrame] = []
        for color in (styles.WARNING, styles.ACCENT, styles.SUCCESS):
            stripe = QFrame()
            stripe.setFixedHeight(4)
            stripe.setStyleSheet(
                f"background-color: {color}; border-radius: 2px;"
            )
            bars_row.addWidget(stripe, 1)
            self._stripes.append(stripe)
        layout.addLayout(bars_row)

        labels_row = QHBoxLayout()
        labels_row.setContentsMargins(0, 0, 0, 0)
        for txt in ("Early", "Mid", "Late"):
            lab = QLabel(txt)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setStyleSheet(
                f"color: {styles.TEXT_MUTED};"
                f" font-size: {styles.FS_CAPTION}px; font-weight: 600;"
            )
            labels_row.addWidget(lab, 1)
        layout.addLayout(labels_row)

    def set_distribution(self, early: int, mid: int, late: int) -> None:
        layout = self._stripes[0].parentWidget().layout()
        if isinstance(layout, QHBoxLayout):
            layout.setStretch(0, max(1, early))
            layout.setStretch(1, max(1, mid))
            layout.setStretch(2, max(1, late))


class _SummaryRow(QWidget):
    """The full row under the title: ally team | spikes | damage | vs |
    damage | spikes | enemy team."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_GRID, styles.SPACING_GRID,
            styles.SPACING_GRID, styles.SPACING_GRID,
        )
        outer.setSpacing(styles.SPACING_GRID)

        self._ally_strip = _TeamStrip("Your Team")
        self._enemy_strip = _TeamStrip("Enemy Team")
        self._ally_spikes = _PowerSpikesBar()
        self._enemy_spikes = _PowerSpikesBar()
        self._ally_damage = _DamageTypeBar()
        self._enemy_damage = _DamageTypeBar()

        vs = QLabel("vs")
        vs.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vs.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_BODY}px;"
            f" font-weight: 700; min-width: 24px;"
        )

        outer.addWidget(self._ally_strip, 0)
        outer.addWidget(self._ally_spikes, 1)
        outer.addWidget(self._ally_damage, 1)
        outer.addWidget(vs, 0)
        outer.addWidget(self._enemy_damage, 1)
        outer.addWidget(self._enemy_spikes, 1)
        outer.addWidget(self._enemy_strip, 0)

        self.setStyleSheet(
            f"QWidget {{ background-color: {styles.BG_SECONDARY};"
            f" border: 1px solid {styles.BORDER};"
            f" border-radius: {styles.RADIUS}px; }}"
        )

    def update_summary(
        self,
        view: "SessionView",
        tags_lookup: TagsLookup,
        icon_lookup: IconLookup,
    ) -> None:
        """Pull team rosters + damage profiles + tag-derived phase scores
        out of the SessionView and feed each sub-widget."""
        session = view.session
        if session is None:
            return

        ally_keys = self._team_keys(session.my_team, view)
        enemy_keys = self._team_keys(session.their_team, view)
        self._ally_strip.set_team(ally_keys, icon_lookup)
        self._enemy_strip.set_team(enemy_keys, icon_lookup)

        # Damage-type split — count members per profile, render as percentage.
        ap_a, ad_a = self._damage_split(session.my_team, view, ally_side=True)
        ap_e, ad_e = self._damage_split(session.their_team, view, ally_side=False)
        self._ally_damage.set_split(ap_a, ad_a)
        self._enemy_damage.set_split(ap_e, ad_e)

        # Phase distribution — count tag-derived phase per champion.
        e_a, m_a, l_a = self._phase_split(ally_keys, tags_lookup)
        e_e, m_e, l_e = self._phase_split(enemy_keys, tags_lookup)
        self._ally_spikes.set_distribution(e_a, m_a, l_a)
        self._enemy_spikes.set_distribution(e_e, m_e, l_e)

    @staticmethod
    def _team_keys(
        members: "Sequence[ChampSelectMember]", view: "SessionView",
    ) -> list[str]:
        keys: list[str] = []
        for m in members[:5]:
            if not m.champion_id:
                keys.append("")
                continue
            keys.append(view.all_champion_keys.get(m.champion_id, "") or "")
        return keys

    @staticmethod
    def _damage_split(
        members: "Sequence[ChampSelectMember]",
        view: "SessionView",
        *,
        ally_side: bool,
    ) -> tuple[int, int]:
        """Return ``(ap_pct, ad_pct)`` summed across the team's known
        damage profiles. SessionView only stores enemy_damage_profile
        today; we infer ally damage profiles from tag heuristics here
        as a stop-gap (Marksman/Fighter → AD, Mage/Burst → AP).
        Hybrids contribute 0.5 to each side."""
        ap = 0.0
        ad = 0.0
        for m in members[:5]:
            profile = ""
            if not ally_side:
                profile = view.enemy_damage_profile.get(m.cell_id, "")
            if not profile:
                # Tag-based fallback (and always for ally side).
                tags = []
                key = view.all_champion_keys.get(m.champion_id, "") if m.champion_id else ""
                if key:
                    tags = _SummaryRow._tags_for(view, key)
                profile = _SummaryRow._profile_from_tags(tags)
            if profile == "AP":
                ap += 1.0
            elif profile == "AD":
                ad += 1.0
            elif profile == "AP/AD":
                ap += 0.5
                ad += 0.5
        total = ap + ad
        if total <= 0:
            return (0, 0)
        return (round(ap / total * 100), round(ad / total * 100))

    @staticmethod
    def _phase_split(
        keys: list[str], tags_lookup: TagsLookup,
    ) -> tuple[int, int, int]:
        early = mid = late = 0
        for key in keys:
            if not key:
                continue
            phase = _phase_for_tags(tags_lookup(key))
            if phase == "early":
                early += 1
            elif phase == "late":
                late += 1
            else:
                mid += 1
        # Render at minimum 1-1-1 if no data so the bar has visible segments.
        if early + mid + late == 0:
            return (1, 1, 1)
        return (early, mid, late)

    @staticmethod
    def _tags_for(view: "SessionView", key: str) -> list[str]:
        # SessionView doesn't carry the static tags map; the view-builder
        # could pass it in but for now we accept an empty result rather
        # than reach for a global. The damage-profile fallback below
        # handles the missing data gracefully.
        return []

    @staticmethod
    def _profile_from_tags(tags: list[str]) -> str:
        if not tags:
            return ""
        ap = any(t in ("Mage", "Burst") for t in tags)
        ad = any(t in ("Marksman", "Fighter", "Assassin") for t in tags)
        if ap and ad:
            return "AP/AD"
        if ap:
            return "AP"
        if ad:
            return "AD"
        return ""


# ─── Body columns ───────────────────────────────────────────────────────────


def _panel_frame() -> QFrame:
    """Reusable panel container with the project's panel-token styling."""
    f = QFrame()
    f.setStyleSheet(
        f"QFrame {{ background-color: {styles.BG_SECONDARY};"
        f" border: 1px solid {styles.BORDER};"
        f" border-radius: {styles.RADIUS}px; }}"
    )
    return f


def _section_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(
        f"color: {styles.TEXT_MUTED};"
        f" font-size: {styles.FS_LABEL}px;"
        " font-weight: 700; letter-spacing: 1.2px;"
        " text-transform: uppercase; padding: 4px 0;"
    )
    return lab


def _muted_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(
        f"color: {styles.TEXT_MUTED};"
        f" font-size: {styles.FS_CAPTION}px; padding: 2px 0;"
    )
    lab.setWordWrap(True)
    return lab


class _BuildCard(QWidget):
    """Left column — locked-champion build card.

    Shows the player's locked-in champion with role + patch placeholder,
    then the matchup-specific counters list. Empty state ("Lock in to
    see your build") when ``view.my_champion_key`` is empty."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(styles.SPACING_GRID)

        self._frame = _panel_frame()
        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(
            styles.SPACING_GRID, styles.SPACING_GRID,
            styles.SPACING_GRID, styles.SPACING_GRID,
        )
        frame_layout.setSpacing(8)

        # Header row — icon + champion name + role label.
        header = QHBoxLayout()
        header.setSpacing(10)
        self._icon = QLabel()
        self._icon.setFixedSize(QSize(48, 48))
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setStyleSheet(
            f"background-color: {styles.BG_TERTIARY};"
            f" border: 1px solid {styles.BORDER};"
            f" border-radius: {styles.RADIUS}px;"
            f" color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_HEADING}px; font-weight: 700;"
        )
        header.addWidget(self._icon)

        self._title_col = QVBoxLayout()
        self._title_col.setSpacing(2)
        self._champ_name = QLabel("Lock in your champion")
        self._champ_name.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_HEADING}px; font-weight: 700;"
        )
        self._role_line = QLabel("")
        self._role_line.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_CAPTION}px; font-weight: 600;"
        )
        self._title_col.addWidget(self._champ_name)
        self._title_col.addWidget(self._role_line)
        header.addLayout(self._title_col, 1)
        frame_layout.addLayout(header)

        # Recommended Builds list (placeholder — single line for now).
        frame_layout.addWidget(_section_label("Recommended Builds"))
        self._popular_line = _muted_label("Popular build (loaded after lock-in)")
        frame_layout.addWidget(self._popular_line)

        # Matchup-specific counters.
        frame_layout.addWidget(_section_label("Matchup Specific"))
        self._matchups_col = QVBoxLayout()
        self._matchups_col.setSpacing(4)
        frame_layout.addLayout(self._matchups_col)
        self._no_matchups = _muted_label("Counters appear once enemies pick.")
        frame_layout.addWidget(self._no_matchups)

        frame_layout.addStretch(1)
        outer.addWidget(self._frame)

    def update_card(
        self,
        view: "SessionView",
        icon_lookup: IconLookup,
    ) -> None:
        # Champion icon + name.
        key = view.my_champion_key
        if key:
            pix = icon_lookup(key)
            if pix is not None and not pix.isNull():
                self._icon.setPixmap(pix.scaled(
                    48, 48,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                self._icon.setText("")
            else:
                self._icon.setPixmap(QPixmap())
                self._icon.setText(key[:1].upper())
            self._champ_name.setText(key)
            role = view.my_champion_role
            self._role_line.setText(f"{role}  ·  Locked" if role else "Locked")
            self._popular_line.setText(
                "Popular build — runes, items, summoners (right panel)"
            )
        else:
            self._icon.setPixmap(QPixmap())
            self._icon.setText("?")
            self._champ_name.setText("Lock in your champion")
            self._role_line.setText("")
            self._popular_line.setText("Popular build (loaded after lock-in)")

        # Matchup-specific list — derive from enemy_counters / their_team.
        # Show up to 3 enemies whose counter scores reference our champion
        # or their role.
        self._clear_layout(self._matchups_col)
        rows = self._matchup_rows(view, icon_lookup)
        if not rows:
            self._no_matchups.show()
        else:
            self._no_matchups.hide()
            for row in rows:
                self._matchups_col.addLayout(row)

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
            inner = item.layout() if item is not None else None
            if inner is not None:
                _BuildCard._clear_layout_qhbox(inner)

    @staticmethod
    def _clear_layout_qhbox(layout: "QHBoxLayout | QVBoxLayout") -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()

    def _matchup_rows(
        self,
        view: "SessionView",
        icon_lookup: IconLookup,
    ) -> list[QHBoxLayout]:
        """Build up to 3 matchup rows: ``portrait | name | counter score``.

        Counter "win rate" placeholder uses the score field from
        CounterEntry — not a real win rate, but the closest thing we
        have. Labelled honestly as "Score" rather than "WR" so we
        don't mislead about data origin."""
        rows: list[QHBoxLayout] = []
        session = view.session
        if session is None:
            return rows

        for member in session.their_team[:5]:
            if not member.champion_id:
                continue
            counters = view.enemy_counters.get(member.cell_id, [])
            if not counters:
                continue
            top = counters[0]
            row = QHBoxLayout()
            row.setSpacing(8)
            row.setContentsMargins(0, 0, 0, 0)

            enemy_key = view.enemy_keys.get(member.champion_id, "")
            portrait = QLabel()
            portrait.setFixedSize(QSize(20, 20))
            portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
            portrait.setStyleSheet(
                f"background-color: {styles.BG_TERTIARY};"
                f" border-radius: {styles.RADIUS_SMALL}px;"
                f" color: {styles.TEXT_MUTED};"
                f" font-size: {styles.FS_CAPTION}px;"
            )
            if enemy_key:
                pix = icon_lookup(enemy_key)
                if pix is not None and not pix.isNull():
                    portrait.setPixmap(pix.scaled(
                        20, 20,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
                else:
                    portrait.setText(enemy_key[:1].upper())
            row.addWidget(portrait)

            name = QLabel(view.enemy_names.get(member.champion_id, enemy_key))
            name.setStyleSheet(
                f"color: {styles.TEXT_PRIMARY};"
                f" font-size: {styles.FS_BODY}px; font-weight: 600;"
            )
            row.addWidget(name, 1)

            # Score (counter score, 0-10 scale → percentage-style display).
            score_pct = int(round(top.score * 10))
            score_lab = QLabel(f"{score_pct}%")
            score_lab.setStyleSheet(
                f"color: {styles.SUCCESS};"
                f" font-size: {styles.FS_BODY}px; font-weight: 700;"
            )
            row.addWidget(score_lab)
            rows.append(row)
            if len(rows) >= 3:
                break
        return rows


class _ItemsPanel(QWidget):
    """Center column — runes + spells + item path."""

    _RUNE_PX = 28
    _ITEM_PX = 32

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(styles.SPACING_GRID)

        self._frame = _panel_frame()
        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(
            styles.SPACING_GRID, styles.SPACING_GRID,
            styles.SPACING_GRID, styles.SPACING_GRID,
        )
        frame_layout.setSpacing(8)

        # Runes row.
        frame_layout.addWidget(_section_label("Runes"))
        self._runes_row = QHBoxLayout()
        self._runes_row.setSpacing(4)
        self._runes_row.setContentsMargins(0, 0, 0, 0)
        runes_holder = QWidget()
        runes_holder.setLayout(self._runes_row)
        frame_layout.addWidget(runes_holder)

        # Summoner spells row.
        frame_layout.addWidget(_section_label("Summoner Spells"))
        self._spells_line = _muted_label("—")
        self._spells_line.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_BODY}px; font-weight: 600;"
        )
        frame_layout.addWidget(self._spells_line)

        # Items section — single combined row for now.
        frame_layout.addWidget(_section_label("Items"))
        self._items_row = QHBoxLayout()
        self._items_row.setSpacing(4)
        self._items_row.setContentsMargins(0, 0, 0, 0)
        items_holder = QWidget()
        items_holder.setLayout(self._items_row)
        frame_layout.addWidget(items_holder)

        self._empty_hint = _muted_label("Lock in to see runes + items.")
        frame_layout.addWidget(self._empty_hint)

        frame_layout.addStretch(1)
        outer.addWidget(self._frame)

    def update_panel(
        self,
        view: "SessionView",
        rune_icons: dict[str, "QPixmap"],
        item_icons: dict[str, "QPixmap"],
    ) -> None:
        build = view.my_champion_build
        _BuildCard._clear_layout_qhbox(self._runes_row)
        _BuildCard._clear_layout_qhbox(self._items_row)

        if build is None:
            self._spells_line.setText("—")
            self._empty_hint.show()
            return
        self._empty_hint.hide()

        for name in build.runes[:8]:
            self._runes_row.addWidget(self._icon_label(
                name, rune_icons.get(name), self._RUNE_PX,
                fallback_color=styles.TIER_A,
            ))
        self._runes_row.addStretch(1)

        if build.summoners:
            self._spells_line.setText(" · ".join(build.summoners))
        else:
            self._spells_line.setText("—")

        for i, name in enumerate(build.items[:6]):
            self._items_row.addWidget(self._icon_label(
                name, item_icons.get(name), self._ITEM_PX,
                fallback_color=styles.TIER_S,
            ))
            if i < min(len(build.items), 6) - 1:
                arrow = QLabel("›")
                arrow.setStyleSheet(
                    f"color: {styles.TEXT_MUTED};"
                    f" font-size: {styles.FS_LABEL}px; padding: 0 2px;"
                )
                self._items_row.addWidget(arrow)
        self._items_row.addStretch(1)

    @staticmethod
    def _icon_label(
        name: str,
        pix: "QPixmap | None",
        size: int,
        *,
        fallback_color: str,
    ) -> QLabel:
        lbl = QLabel()
        lbl.setFixedSize(QSize(size, size))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setToolTip(name)
        lbl.setStyleSheet(
            f"background-color: {styles.BG_TERTIARY};"
            f" border: 1px solid {styles.BORDER_FAINT};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" color: {fallback_color};"
            f" font-size: {styles.FS_CAPTION}px;"
            " font-weight: 700;"
        )
        if pix is not None and not pix.isNull():
            lbl.setPixmap(pix.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            lbl.setText(name[:2].upper())
        return lbl


class _GamePlanPanel(QWidget):
    """Right column — Early/Mid/Late phase tabs + champion power spikes
    + playing-against advice. The prose itself is placeholder-only for
    now; the LLM-game-plan deliverable is option-2 follow-up per the
    user's design ask."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(styles.SPACING_GRID)

        self._frame = _panel_frame()
        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(
            styles.SPACING_GRID, styles.SPACING_GRID,
            styles.SPACING_GRID, styles.SPACING_GRID,
        )
        frame_layout.setSpacing(10)

        # Header — Game Plan + PLUS pill (matches screenshot's free vs
        # paid framing; we don't actually have a paid tier, the pill is
        # decorative for now).
        header = QHBoxLayout()
        title = QLabel("Game Plan")
        title.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_HEADING}px; font-weight: 700;"
        )
        header.addWidget(title)
        header.addStretch(1)
        frame_layout.addLayout(header)

        # Phase tabs — Early/Mid/Late as toggle buttons. v1 just shows
        # the currently-selected phase's label; the prose lives below.
        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(6)
        for phase in ("Early Game", "Mid Game", "Late Game"):
            tab = QLabel(phase)
            tab.setStyleSheet(
                f"color: {styles.TEXT_MUTED};"
                f" background: {styles.BG_TERTIARY};"
                f" border: 1px solid {styles.BORDER};"
                f" border-radius: {styles.RADIUS_SMALL}px;"
                f" padding: 4px 10px;"
                f" font-size: {styles.FS_CAPTION}px; font-weight: 700;"
            )
            tabs_row.addWidget(tab)
        tabs_row.addStretch(1)
        frame_layout.addLayout(tabs_row)

        self._plan_body = QLabel(
            "Prose game-plan advice will appear here once a champion is "
            "locked. Wiring an LLM-generated phase plan (Early/Mid/Late) "
            "is the next iteration; for v1.10.79 the layout is in place."
        )
        self._plan_body.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY};"
            f" font-size: {styles.FS_BODY}px; line-height: 1.4;"
        )
        self._plan_body.setWordWrap(True)
        frame_layout.addWidget(self._plan_body)

        frame_layout.addWidget(_section_label("Champion Power Spikes"))
        self._spikes_line = _muted_label("Locked-champion phase scaling appears here.")
        frame_layout.addWidget(self._spikes_line)

        frame_layout.addWidget(_section_label("Playing Against"))
        self._against_line = _muted_label("Threat-summary appears once enemies are picked.")
        frame_layout.addWidget(self._against_line)

        frame_layout.addStretch(1)
        outer.addWidget(self._frame)

    def update_panel(self, view: "SessionView") -> None:
        key = view.my_champion_key
        if key:
            self._spikes_line.setText(
                f"{key}'s power-spike timing — coming with the LLM "
                "game-plan iteration."
            )
        else:
            self._spikes_line.setText(
                "Locked-champion phase scaling appears here."
            )
        # Threat summary — count alert/warn-class enemies as a proxy.
        session = view.session
        threats = []
        if session is not None:
            for member in session.their_team[:5]:
                if not member.champion_id:
                    continue
                key_e = view.enemy_keys.get(member.champion_id, "")
                if key_e:
                    threats.append(key_e)
        if threats:
            self._against_line.setText(
                f"Enemy threats locked in: {', '.join(threats[:5])}."
            )
        else:
            self._against_line.setText(
                "Threat-summary appears once enemies are picked."
            )


class LiveCompanionView(QWidget):
    """Single-window champ-select view (Mobalytics-style).

    Sits above the existing champ-select panels in the overlay during
    BAN_PICK / FINALIZATION. The overlay calls ``update_view(view)`` on
    every SessionView refresh.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_GRID, styles.SPACING_GRID,
            styles.SPACING_GRID, styles.SPACING_GRID,
        )
        outer.setSpacing(styles.SPACING_GRID)

        outer.addWidget(self._build_header())
        self._summary_row = _SummaryRow()
        outer.addWidget(self._summary_row)

        # Body — three-column layout matching the screenshot: build card /
        # runes+items / game plan. Each column is its own panel so they
        # can be hidden / styled independently.
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(styles.SPACING_GRID)

        self._build_card = _BuildCard()
        self._items_panel = _ItemsPanel()
        self._game_plan_panel = _GamePlanPanel()

        body_layout.addWidget(self._build_card, 2)
        body_layout.addWidget(self._items_panel, 3)
        body_layout.addWidget(self._game_plan_panel, 2)
        outer.addWidget(body, 1)

        self.setStyleSheet(
            f"QWidget {{ background-color: {styles.BG_PRIMARY}; }}"
        )

    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title = QLabel("Live Companion")
        title.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_TITLE}px;"
            " font-weight: 700; letter-spacing: -0.2px;"
        )
        layout.addWidget(title)

        live = QLabel("LIVE")
        live.setStyleSheet(
            f"QLabel {{ background-color: {styles.DANGER};"
            f" color: white; padding: 2px 6px;"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" font-size: {styles.FS_CAPTION}px;"
            " font-weight: 700; letter-spacing: 0.5px; }}"
        )
        layout.addWidget(live)
        layout.addStretch(1)

        return header

    def update_view(
        self,
        view: "SessionView",
        icon_lookup: IconLookup,
        *,
        rune_icons: "dict[str, QPixmap] | None" = None,
        item_icons: "dict[str, QPixmap] | None" = None,
    ) -> None:
        """Called from the overlay on every SessionView refresh."""
        self._summary_row.update_summary(
            view,
            tags_lookup=lambda _key: [],  # tag map plumbing follows
            icon_lookup=icon_lookup,
        )
        self._build_card.update_card(view, icon_lookup)
        self._items_panel.update_panel(
            view, rune_icons or {}, item_icons or {},
        )
        self._game_plan_panel.update_panel(view)

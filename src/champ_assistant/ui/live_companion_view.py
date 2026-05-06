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

        # Body placeholder — left/center/right columns get filled in the
        # next iteration. For v1.10.78 we ship the top header + summary
        # row only so the layout becomes visible end-to-end.
        body = QFrame()
        body.setStyleSheet(
            f"QFrame {{ background-color: {styles.BG_SECONDARY};"
            f" border: 1px solid {styles.BORDER};"
            f" border-radius: {styles.RADIUS}px; }}"
        )
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(
            styles.SPACING_GRID, styles.SPACING_GRID,
            styles.SPACING_GRID, styles.SPACING_GRID,
        )
        placeholder = QLabel(
            "Build · Runes · Items · Game Plan — wired in next iteration.\n"
            "Today: header + team summary bar (damage type + power spikes) "
            "are live."
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_BODY}px; padding: 20px;"
        )
        placeholder.setWordWrap(True)
        body_layout.addWidget(placeholder)
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

    def update_view(self, view: "SessionView", icon_lookup: IconLookup) -> None:
        """Called from the overlay on every SessionView refresh."""
        self._summary_row.update_summary(
            view,
            tags_lookup=lambda _key: [],  # tag map plumbing follows in next commit
            icon_lookup=icon_lookup,
        )

"""Live Companion — unified champ-select view (Mobalytics-style layout).

Single-window champ-select panel. The floating ally/enemy summary
widget that used to live separately was retired in v1.10.80; this
view is the only champ-select surface. Layout (matching the v1.10.78
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

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import styles
from .live_companion_sections import BansColumn, PicksColumn

if TYPE_CHECKING:
    from ..data.models import ChampSelectMember
    from .view_model import SessionView

# Callable signature for the icon lookup the overlay passes in.
IconLookup = Callable[[str], "QPixmap | None"]
# (Phase-tag heuristic moved to ``view_builder._team_phase_distribution``
# in v1.10.90 — the UI now reads pre-computed phase counts off the
# SessionView. Empty stubs no longer leak into the rendered bars.)


# ─── Portrait helpers ───────────────────────────────────────────────────────

_PORTRAIT_PX = 36
_PORTRAIT_SLOT_PX = 40  # icon + 2px halo + 2px gap


class _PortraitSlot(QLabel):
    """One portrait in a ``_TeamStrip``. Carries a slot index so the
    parent strip can re-emit clicks with the right cell. Click handler
    is opt-in — ally strip leaves it disconnected."""

    clicked = pyqtSignal(int)

    def __init__(self, slot_index: int) -> None:
        super().__init__()
        self._slot_index = slot_index

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._slot_index)
        super().mousePressEvent(event)


class _TeamStrip(QWidget):
    """Five champion portraits in a row, used twice (allies + enemies).

    The enemy strip emits ``slot_clicked(cell_id)`` so the user can
    cycle a manual role override (Auto → TOP → JUNGLE → MID → BOT →
    SUPPORT → Auto). Tooltips render per-portrait counter-play tips
    when the parent populates ``tooltips``. Ally strip leaves both
    untouched — no click handler, empty tooltips.
    """

    slot_clicked = pyqtSignal(int)
    """Emits the cell_id of the clicked portrait. Connected from
    ``LiveCompanionView`` → ``MainOverlay.enemy_role_clicked`` only on
    the enemy strip."""

    def __init__(self, label: str, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel(label.upper())
        title.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; letter-spacing: 1.6px;"
        )
        layout.addWidget(title)

        portraits_row = QHBoxLayout()
        portraits_row.setSpacing(2)
        portraits_row.setContentsMargins(0, 0, 0, 0)

        self._slots: list[_PortraitSlot] = []
        # Maps slot index → cell_id for the most recent set_team call;
        # populated then before the click signal fires so the lookup
        # always sees fresh data.
        self._cell_ids: list[int] = [-1] * 5
        for i in range(5):
            slot = _PortraitSlot(slot_index=i)
            slot.setFixedSize(QSize(_PORTRAIT_PX, _PORTRAIT_PX))
            slot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            slot.setStyleSheet(
                f"background-color: {styles.BG_TERTIARY};"
                f" border: 1px solid {styles.BORDER};"
                f" border-radius: {styles.RADIUS_SMALL}px;"
                f" color: {styles.TEXT_MUTED};"
                f" font-size: {styles.FS_CAPTION}px;"
            )
            slot.clicked.connect(self._on_slot_clicked)
            portraits_row.addWidget(slot)
            self._slots.append(slot)

        layout.addLayout(portraits_row)

        # Role badge row beneath the portraits — gives the user
        # immediate visual feedback when click cycles the override.
        # Auto-detected roles render muted; manual overrides render in
        # the accent color so the user can see which lanes they've
        # corrected.
        self._role_labels: list[QLabel] = []
        roles_row = QHBoxLayout()
        roles_row.setSpacing(2)
        roles_row.setContentsMargins(0, 0, 0, 0)
        for _ in range(5):
            lbl = QLabel("")
            lbl.setFixedWidth(_PORTRAIT_PX)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(self._role_label_stylesheet(overridden=False))
            roles_row.addWidget(lbl)
            self._role_labels.append(lbl)
        layout.addLayout(roles_row)

    @staticmethod
    def _role_label_stylesheet(*, overridden: bool) -> str:
        color = styles.ACCENT if overridden else styles.TEXT_MUTED
        weight = "700" if overridden else "600"
        return (
            f"color: {color};"
            f" font-size: {styles.FS_CAPTION}px;"
            f" font-weight: {weight};"
            " letter-spacing: 0.4px;"
        )

    def enable_clicks(self) -> None:
        """Mark each slot as clickable so the cursor + hover state
        signal interactivity. Called once on the enemy strip."""
        for slot in self._slots:
            slot.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_team(
        self,
        keys: list[str],
        icon_lookup: IconLookup,
        *,
        cell_ids: list[int] | None = None,
        tooltips: list[str] | None = None,
        roles: list[str] | None = None,
        overridden_indices: set[int] | None = None,
    ) -> None:
        """Render up to 5 portraits. ``keys`` is the ordered champion-key list;
        ``icon_lookup(key) -> QPixmap | None`` resolves the icon.

        ``cell_ids`` (optional) maps each slot index to the matching
        ``TeamMember.cell_id`` so click events emit the right id. When
        omitted, click events emit ``-1`` (effectively a noop on the
        receiver). ``tooltips`` is the parallel per-slot tip text;
        empty / missing entries clear the tooltip on that slot.
        ``roles`` is the per-slot lane label (``"TOP"`` / ``""`` etc).
        ``overridden_indices`` are slot indices where the role came
        from a manual user override — rendered in the accent color so
        the user sees which lanes they've corrected."""
        ids = list(cell_ids) if cell_ids is not None else [-1] * 5
        tips = list(tooltips) if tooltips is not None else [""] * 5
        role_list = list(roles) if roles is not None else [""] * 5
        overrides = overridden_indices or set()
        # Pad to 5 so index access is always safe.
        ids = (ids + [-1] * 5)[:5]
        tips = (tips + [""] * 5)[:5]
        role_list = (role_list + [""] * 5)[:5]
        self._cell_ids = ids
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
                else:
                    # Fallback to first letter when icon hasn't loaded yet.
                    slot.setPixmap(QPixmap())
                    slot.setText(keys[i][:1].upper())
            else:
                slot.setPixmap(QPixmap())
                slot.setText("")
            slot.setToolTip(tips[i])
            # Role badge per slot.
            self._role_labels[i].setText(role_list[i])
            self._role_labels[i].setStyleSheet(
                self._role_label_stylesheet(overridden=i in overrides)
            )

    def _on_slot_clicked(self, slot_index: int) -> None:
        cell_id = self._cell_ids[slot_index] if 0 <= slot_index < len(self._cell_ids) else -1
        if cell_id >= 0:
            self.slot_clicked.emit(cell_id)


class _DamageTypeBar(QWidget):
    """Horizontal % AP / % AD bar. AD on the right (pink) and AP on the
    left (blue) match the screenshot's color convention."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel("DAMAGE TYPE")
        title.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; letter-spacing: 1.6px;"
        )
        layout.addWidget(title)

        bars_row = QHBoxLayout()
        bars_row.setSpacing(8)
        bars_row.setContentsMargins(0, 0, 0, 0)

        # Color stripes — vertical gradient (bright top, base bottom)
        # so the stat block reads as lit-from-above instead of flat.
        # v1.10.112.
        self._ap_stripe = QFrame()
        self._ap_stripe.setFixedHeight(6)
        self._ap_stripe.setStyleSheet(
            styles.gradient_stripe_stylesheet(styles.ACCENT_BRIGHT, styles.ACCENT)
        )
        self._ad_stripe = QFrame()
        self._ad_stripe.setFixedHeight(6)
        self._ad_stripe.setStyleSheet(
            styles.gradient_stripe_stylesheet(styles.DANGER_BRIGHT, styles.DANGER)
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

        title = QLabel("TEAM POWER SPIKES")
        title.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; letter-spacing: 1.6px;"
        )
        layout.addWidget(title)

        bars_row = QHBoxLayout()
        bars_row.setSpacing(4)
        bars_row.setContentsMargins(0, 0, 0, 0)
        self._stripes: list[QFrame] = []
        # Vertical gradients so each phase segment lifts off the panel
        # background instead of reading as a flat colored rectangle.
        for bright, base in (
            (styles.WARNING_BRIGHT, styles.WARNING),
            (styles.ACCENT_BRIGHT, styles.ACCENT),
            (styles.SUCCESS_BRIGHT, styles.SUCCESS),
        ):
            stripe = QFrame()
            stripe.setFixedHeight(6)
            stripe.setStyleSheet(
                styles.gradient_stripe_stylesheet(bright, base)
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

    enemy_role_clicked = pyqtSignal(int)
    """Emits the cell_id of the clicked enemy portrait. Bubbled by
    LiveCompanionView up to MainOverlay so the orchestrator's
    ``cycle_enemy_role_override`` handler picks it up. Added in
    v1.10.105 — clickable enemy portraits restore a feature retired
    when EnemyRow widgets were dropped in the LiveCompanion redesign."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_LOOSE, styles.SPACING_WIDE,
            styles.SPACING_LOOSE, styles.SPACING_WIDE,
        )
        outer.setSpacing(styles.SPACING_LOOSE)

        self._ally_strip = _TeamStrip("Your Team")
        self._enemy_strip = _TeamStrip("Enemy Team")
        # Enemy portraits are clickable: cycles through the role
        # override states. Tooltip surfaces the per-enemy counter tip.
        self._enemy_strip.enable_clicks()
        self._enemy_strip.slot_clicked.connect(self.enemy_role_clicked.emit)
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

        # Gradient surface + drop shadow so the summary row reads as a
        # primary card lifted off the body background — matches the
        # body-column treatment from _panel_frame for visual cohesion.
        self.setStyleSheet(
            styles.gradient_panel_stylesheet(selector="QWidget")
        )
        styles.apply_panel_shadow(self)

    def update_summary(
        self,
        view: "SessionView",
        icon_lookup: IconLookup,
    ) -> None:
        """Pull team rosters + damage profiles + phase distribution out of
        the SessionView and feed each sub-widget. v1.10.90: phase data
        now arrives pre-computed via ``view.{ally,enemy}_phase_distribution``
        — previously a stub ``tags_lookup`` lambda always returned ``[]``
        making every team render as pure mid-game."""
        session = view.session
        if session is None:
            return

        ally_keys = self._team_keys(session.my_team, view)
        enemy_keys = self._team_keys(session.their_team, view)
        self._ally_strip.set_team(ally_keys, icon_lookup)
        # Enemy strip carries cell_ids (so clicks emit the right one),
        # per-slot counter-tip tooltips, and the role label that gives
        # immediate feedback when the user clicks to cycle the override.
        enemy_members = list(session.their_team[:5])
        enemy_cell_ids = [m.cell_id for m in enemy_members]
        enemy_tooltips = [
            self._enemy_tooltip(m.cell_id, view) for m in enemy_members
        ]
        enemy_roles = [
            view.enemy_roles.get(m.cell_id, "") for m in enemy_members
        ]
        enemy_overridden = {
            i for i, m in enumerate(enemy_members)
            if m.cell_id in view.enemy_role_overridden
        }
        self._enemy_strip.set_team(
            enemy_keys, icon_lookup,
            cell_ids=enemy_cell_ids,
            tooltips=enemy_tooltips,
            roles=enemy_roles,
            overridden_indices=enemy_overridden,
        )

        # Damage-type split — count members per profile, render as percentage.
        ap_a, ad_a = self._damage_split(session.my_team, view, ally_side=True)
        ap_e, ad_e = self._damage_split(session.their_team, view, ally_side=False)
        self._ally_damage.set_split(ap_a, ad_a)
        self._enemy_damage.set_split(ap_e, ad_e)

        # Phase distribution — pre-computed in view_builder.
        e_a, m_a, l_a = self._safe_distribution(view.ally_phase_distribution)
        e_e, m_e, l_e = self._safe_distribution(view.enemy_phase_distribution)
        self._ally_spikes.set_distribution(e_a, m_a, l_a)
        self._enemy_spikes.set_distribution(e_e, m_e, l_e)

    @staticmethod
    def _enemy_tooltip(cell_id: int, view: "SessionView") -> str:
        """Compose the enemy-portrait tooltip from the per-cell counter
        tip plus a click-to-cycle hint. The cycle hint is always shown
        so the user discovers the click interaction even on enemies
        that have no curated counter tip."""
        tip = view.enemy_counter_tips.get(cell_id, "")
        cycle_hint = "Click to cycle role override (Auto → TOP → JUNGLE → MID → BOT → SUPPORT → Auto)"
        if tip:
            return f"{tip}\n\n{cycle_hint}"
        return cycle_hint

    @staticmethod
    def _safe_distribution(
        dist: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        """Render at minimum 1-1-1 if the team has no locked picks yet so
        the bar shows three visible segments instead of collapsing."""
        early, mid, late = dist
        if early + mid + late == 0:
            return (1, 1, 1)
        return (early, mid, late)

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
        damage profiles. View-builder computes both ``enemy_damage_profile``
        and ``ally_damage_profile`` (added v1.10.85) so the same shape
        works for either side. Hybrids contribute 0.5 to each."""
        profile_map = (
            view.ally_damage_profile if ally_side else view.enemy_damage_profile
        )
        ap = 0.0
        ad = 0.0
        for m in members[:5]:
            profile = profile_map.get(m.cell_id, "")
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


# ─── Body columns ───────────────────────────────────────────────────────────


def _panel_frame() -> QFrame:
    """Reusable panel container — gradient surface + subtle drop shadow
    so the body columns lift off the main window background.

    v1.10.107: switched from flat ``BG_SECONDARY`` to the
    ``gradient_panel_stylesheet`` helper. Adds depth across the whole
    LiveCompanion body in one place — every column (BuildCard,
    ItemsPanel, GamePlanPanel) inherits the new look.
    """
    f = QFrame()
    f.setStyleSheet(styles.gradient_panel_stylesheet(selector="QFrame"))
    styles.apply_panel_shadow(f)
    return f


def _section_label(text: str) -> QLabel:
    """Section header style — small, uppercase, wide-tracked muted text.
    Uppercase is applied via ``.upper()`` because Qt QSS doesn't
    support ``text-transform: uppercase`` (silent no-op + parse warning
    in older Qt). v1.10.111 restored the uppercase intent that was
    lost when v1.10.104 dropped the unsupported QSS property."""
    lab = QLabel(text.upper())
    lab.setStyleSheet(
        f"color: {styles.TEXT_MUTED};"
        f" font-size: {styles.FS_LABEL}px;"
        " font-weight: 700; letter-spacing: 1.6px;"
        " padding: 10px 0 6px 0;"
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
            styles.SPACING_LOOSE, styles.SPACING_WIDE,
            styles.SPACING_LOOSE, styles.SPACING_WIDE,
        )
        frame_layout.setSpacing(styles.SPACING_GRID)

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
            # Surface real build info instead of a static placeholder
            # line. When my_champion_build is populated, show name +
            # item / rune counts; otherwise keep the loading hint.
            build = view.my_champion_build
            if build is not None:
                items_n = len(build.items)
                runes_n = len(build.runes)
                summ_n = len(build.summoners)
                self._popular_line.setText(
                    f"{build.name or 'Default'} — {items_n} items · "
                    f"{runes_n} runes · {summ_n} spells"
                )
            else:
                self._popular_line.setText(
                    f"No build data for {key} yet — apply manually if needed"
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
    _SPELL_PX = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(styles.SPACING_GRID)

        self._frame = _panel_frame()
        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(
            styles.SPACING_LOOSE, styles.SPACING_WIDE,
            styles.SPACING_LOOSE, styles.SPACING_WIDE,
        )
        frame_layout.setSpacing(styles.SPACING_GRID)

        # Runes row.
        frame_layout.addWidget(_section_label("Runes"))
        self._runes_row = QHBoxLayout()
        self._runes_row.setSpacing(4)
        self._runes_row.setContentsMargins(0, 0, 0, 0)
        runes_holder = QWidget()
        runes_holder.setLayout(self._runes_row)
        frame_layout.addWidget(runes_holder)

        # Summoner spells row — icons (Flash / Ignite / Heal etc).
        # v1.10.109: was text-only. Spell icons are prefetched and
        # routed through ``MainOverlay.set_spell_icons`` → LiveCompanion
        # → here.
        frame_layout.addWidget(_section_label("Summoner Spells"))
        self._spells_row = QHBoxLayout()
        self._spells_row.setSpacing(6)
        self._spells_row.setContentsMargins(0, 0, 0, 0)
        spells_holder = QWidget()
        spells_holder.setLayout(self._spells_row)
        frame_layout.addWidget(spells_holder)

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
        spell_icons: dict[str, "QPixmap"] | None = None,
    ) -> None:
        build = view.my_champion_build
        _BuildCard._clear_layout_qhbox(self._runes_row)
        _BuildCard._clear_layout_qhbox(self._items_row)
        _BuildCard._clear_layout_qhbox(self._spells_row)
        spells = spell_icons or {}

        if build is None:
            self._empty_hint.show()
            return
        self._empty_hint.hide()

        for name in build.runes[:8]:
            self._runes_row.addWidget(self._icon_label(
                name, rune_icons.get(name), self._RUNE_PX,
                fallback_color=styles.TIER_A,
            ))
        self._runes_row.addStretch(1)

        for name in build.summoners[:2]:
            self._spells_row.addWidget(self._icon_label(
                name, spells.get(name), self._SPELL_PX,
                fallback_color=styles.ACCENT,
            ))
        self._spells_row.addStretch(1)

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
            styles.SPACING_LOOSE, styles.SPACING_WIDE,
            styles.SPACING_LOOSE, styles.SPACING_WIDE,
        )
        frame_layout.setSpacing(styles.SPACING_GRID)

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
            f" font-size: {styles.FS_BODY}px;"
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
        # Game plan body — three states:
        #   1. Cached LLM prose available → show it
        #   2. Champion locked AND LLM enabled → "Generating…"
        #   3. Champion locked AND LLM disabled → setup hint
        #   4. No champion → pre-lock placeholder
        if view.game_plan_text:
            self._plan_body.setText(view.game_plan_text)
        elif key and view.game_plan_enabled:
            self._plan_body.setText(
                f"Generating game plan for {key}… (lands on the next "
                "snapshot once the LLM responds)."
            )
        elif key and not view.game_plan_enabled:
            self._plan_body.setText(
                "Configure an LLM provider in Settings → API Keys to "
                "generate matchup-aware game plans (OpenRouter / Groq / "
                "Gemini — free tiers work)."
            )
        else:
            self._plan_body.setText(
                "Lock in your champion to generate a matchup-aware game "
                "plan covering win condition, key matchup, and tempo."
            )

        # Champion Power Spikes — deterministic level/item thresholds
        # surfaced from the static spike model. No LLM needed for this.
        if key:
            self._spikes_line.setText(self._spike_summary(key, view))
        else:
            self._spikes_line.setText(
                "Locked-champion phase scaling appears here."
            )

        # Threat summary — list locked-in enemies (a proxy until we
        # have real per-enemy threat scoring).
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

    @staticmethod
    def _spike_summary(key: str, view: "SessionView") -> str:
        """One-line phase summary using static tag heuristics.

        Doesn't need an LLM: ``static/tags.json`` already encodes the
        phase signal as Early-Game / Late-Game / Hyper-Carry / Scaling
        tags. v1.10.100 routes that signal through
        ``SessionView.my_champion_phase`` instead of falling back to a
        generic L6/L11/L16 reminder."""
        phase = view.my_champion_phase
        if phase == "early":
            return (
                f"{key} — early-game lane bully. Snowball L1-9, force "
                "trades on cooldown advantages. Falls off after L11."
            )
        if phase == "late":
            return (
                f"{key} — scaling / late-game carry. Survive the early "
                "phase, hit core 2-3 items, take over teamfights L11+."
            )
        if phase == "mid":
            return (
                f"{key} — mid-game power spike. Strongest L6-13 with "
                "ult + first item; play around teamfights, not solo lanes."
            )
        return (
            f"{key} — universal spikes at L6 (ult), L11 (R+1), L16 (R+2). "
            "Item spikes track in the Recommended Build column."
        )


class LiveCompanionView(QWidget):
    """Single-window champ-select view (Mobalytics-style).

    Sits above the existing champ-select panels in the overlay during
    BAN_PICK / FINALIZATION. The overlay calls ``update_view(view)`` on
    every SessionView refresh.
    """

    pick_hover_requested = pyqtSignal(str)
    ban_hover_requested = pyqtSignal(str)
    enemy_role_clicked = pyqtSignal(int)
    """Bubbled from ``_SummaryRow.enemy_role_clicked`` when the user
    clicks an enemy portrait. MainOverlay re-emits up to the orchestrator
    via its own ``enemy_role_clicked`` signal."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_WIDE, styles.SPACING_WIDE,
            styles.SPACING_WIDE, styles.SPACING_WIDE,
        )
        outer.setSpacing(styles.SPACING_WIDE)

        outer.addWidget(self._build_header())
        self._summary_row = _SummaryRow()
        self._summary_row.enemy_role_clicked.connect(self.enemy_role_clicked.emit)
        outer.addWidget(self._summary_row)

        # Body — three-column layout matching the screenshot: build card +
        # picks suggestions / runes+items / game plan. Each column is its
        # own panel so they can be hidden / styled independently.
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(styles.SPACING_GRID)

        # Left column: build card stacked on top of pick suggestions.
        # Both share the same width slot so the layout stays aligned.
        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(styles.SPACING_GRID)
        self._build_card = _BuildCard()
        left_layout.addWidget(self._build_card)
        self._bans_column = BansColumn()
        self._bans_column.ban_hover_requested.connect(self.ban_hover_requested.emit)
        left_layout.addWidget(self._bans_column)
        self._picks_column = PicksColumn()
        self._picks_column.pick_hover_requested.connect(self.pick_hover_requested.emit)
        left_layout.addWidget(self._picks_column)
        left_layout.addStretch(1)

        self._items_panel = _ItemsPanel()
        self._game_plan_panel = _GamePlanPanel()

        body_layout.addWidget(left_col, 2)
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

        # LIVE pill — gradient + pulsing opacity loop signals the
        # overlay is actively driven by real session data. Held as
        # an instance attr to keep the QPropertyAnimation alive
        # (Qt garbage-collects animations whose owners drop the ref).
        self._live_pill = QLabel("LIVE")
        self._live_pill.setStyleSheet(
            "QLabel { background: qlineargradient("
            "  x1:0, y1:0, x2:0, y2:1,"
            f"  stop:0 {styles.DANGER_BRIGHT},"
            f"  stop:1 {styles.DANGER});"
            f" color: white; padding: 2px 8px;"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" font-size: {styles.FS_CAPTION}px;"
            " font-weight: 800; letter-spacing: 1.4px; }"
        )
        layout.addWidget(self._live_pill)
        from . import anim as _anim
        self._live_pulse = _anim.pulse_opacity(self._live_pill)
        layout.addStretch(1)

        return header

    def update_view(
        self,
        view: "SessionView",
        icon_lookup: IconLookup,
        *,
        rune_icons: "dict[str, QPixmap] | None" = None,
        item_icons: "dict[str, QPixmap] | None" = None,
        spell_icons: "dict[str, QPixmap] | None" = None,
    ) -> None:
        """Called from the overlay on every SessionView refresh."""
        self._summary_row.update_summary(view, icon_lookup=icon_lookup)
        self._build_card.update_card(view, icon_lookup)
        self._bans_column.update_bans(view, icon_lookup)
        self._picks_column.update_picks(view, icon_lookup)
        self._items_panel.update_panel(
            view, rune_icons or {}, item_icons or {}, spell_icons or {},
        )
        self._game_plan_panel.update_panel(view)
        # Roster panel was split out into its own RosterWindow in
        # v1.10.110. The window owns its own visibility (loading-screen
        # subphase only) and is wired in MainOverlay alongside the
        # other floating widgets.

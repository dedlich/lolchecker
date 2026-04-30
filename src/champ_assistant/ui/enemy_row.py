"""Enemy team row: enemy champion + their counters."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..data.models import CounterEntry, TeamMember
from ..profiling.profile import EnemyProfile
from . import styles
from .badges import RankPill

ICON_SIZE = 32
# Smaller icon for "main champions" row in the profile section. Sized
# to fit the previous reserved profile-line height (FS_CAPTION + 4 ≈
# 14px) plus a bit of breathing room.
MAIN_ICON_SIZE = 18


class EnemyRow(QFrame):
    """One enemy slot showing the locked champion and their counters.

    The role badge is clickable: clicking cycles the manual role override
    (Auto → TOP → JUNGLE → MID → BOT → SUPPORT → Auto). The actual cycling
    happens in the orchestrator; this widget just emits ``role_clicked``
    with the current cell_id when the user taps the button.
    """

    PLACEHOLDER = "—"

    role_clicked = pyqtSignal(int)  # emits cell_id

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("card", True)
        self._cell_id: int = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(ICON_SIZE, ICON_SIZE)
        self._icon_label.setStyleSheet(
            f"background-color: {styles.BG_PRIMARY}; "
            f"border-radius: {styles.RADIUS}px; border: 1px solid {styles.BORDER};"
        )
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._champion_label = QLabel(self.PLACEHOLDER)
        self._champion_label.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-weight: 600;"
        )

        # Tiny AP/AD badge — surfaces the enemy's damage type so the
        # player can prioritize MR vs Armor at a glance. Hidden when
        # the enemy hasn't picked yet or the champion has no clear
        # damage classification (Tank-only, Enchanter).
        self._damage_badge = QLabel("")
        self._damage_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._damage_badge.setMinimumWidth(34)
        self._damage_badge.hide()

        self._role_button = QPushButton("")
        self._role_button.setFlat(True)
        self._role_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._role_button.setToolTip(
            "Click to override role: Auto → TOP → JUNGLE → MID → BOT → SUPPORT"
        )
        self._role_button.clicked.connect(self._on_role_clicked)
        self._update_role_button_style(role="", overridden=False)

        head.addWidget(self._icon_label)
        head.addWidget(self._champion_label)
        head.addWidget(self._damage_badge)
        head.addStretch()

        self._rank_pill = RankPill()
        self._rank_pill.hide()
        head.addWidget(self._rank_pill)

        head.addWidget(self._role_button)

        self._counters_label = QLabel("")
        self._counters_label.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
        )
        self._counters_label.setWordWrap(True)

        # Optional profiling row — three small main-champion icons +
        # stats text. Kept always-visible with empty placeholders so
        # async profile data arriving doesn't cause a layout shift
        # (P5: no sudden size changes).
        self._mains_row = QHBoxLayout()
        self._mains_row.setSpacing(4)
        self._mains_row.setContentsMargins(0, 0, 0, 0)
        self._main_icons: list[QLabel] = []
        for _ in range(3):
            icon = QLabel()
            icon.setFixedSize(MAIN_ICON_SIZE, MAIN_ICON_SIZE)
            icon.setScaledContents(True)
            icon.setStyleSheet(
                f"background-color: {styles.BG_PRIMARY};"
                f" border-radius: {styles.RADIUS_SMALL}px;"
                f" border: 1px solid {styles.BORDER_FAINT};"
            )
            icon.hide()
            self._main_icons.append(icon)
            self._mains_row.addWidget(icon)

        # Text part of the profile line (stats only — no longer the
        # "Mains: A, B, C" prefix since those are visualized as icons
        # to the left).
        self._profile_label = QLabel(" ")  # nbsp keeps height = 1 line
        self._profile_label.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY}; font-size: {styles.FS_CAPTION}px;"
            f" min-height: {MAIN_ICON_SIZE}px;"
        )
        self._profile_label.setWordWrap(False)
        self._profile_label.setMinimumHeight(MAIN_ICON_SIZE)
        self._mains_row.addWidget(self._profile_label, 1)

        outer.addLayout(head)
        outer.addWidget(self._counters_label)
        outer.addLayout(self._mains_row)

        self.clear()

    def clear(self) -> None:
        self._cell_id = -1
        self._champion_label.setText(self.PLACEHOLDER)
        self._update_role_button_style(role="", overridden=False)
        self._update_damage_badge("")
        self._counters_label.setText("")
        self._icon_label.clear()
        self._profile_label.setText(" ")  # keep reserved space
        for icon in self._main_icons:
            icon.clear()
            icon.hide()
        self._rank_pill.hide()

    def set_profile(
        self,
        profile: EnemyProfile | None,
        *,
        champion_names: dict[int, str] | None = None,
        champion_keys: dict[int, str] | None = None,
        icon_lookup=None,  # type: ignore[no-untyped-def]
    ) -> None:
        """Render an optional pre-game profile.

        Layout:
          rank pill (in the header, top-right corner)
          mains row: 3 small champion icons + stats text (WR · streak)

        ``icon_lookup`` is a callable ``key → QPixmap | None`` used to
        resolve main-champion icons. ``champion_keys`` maps the
        numeric champion_id from mastery entries → the string key the
        icon_lookup expects. Both are optional — when missing, the
        mains icons stay hidden and only the stats text renders.

        The mains row always reserves its vertical space (one
        MAIN_ICON_SIZE) so async data doesn't trigger layout shifts.
        """
        if profile is None or not profile.has_data:
            self._profile_label.setText(" ")  # keep reserved space
            for icon in self._main_icons:
                icon.clear()
                icon.hide()
            self._rank_pill.hide()
            return

        if profile.rank.is_ranked:
            self._rank_pill.set_rank(
                tier=profile.rank.tier,
                division=profile.rank.division,
                lp=profile.rank.league_points,
            )
            self._rank_pill.show()
        else:
            self._rank_pill.hide()

        # Mains as icons (max 3). Hide unused slots so empty positions
        # don't reserve visual space.
        names = champion_names or {}
        keys = champion_keys or {}
        for i, icon_label in enumerate(self._main_icons):
            if i < len(profile.top_champions) and icon_lookup is not None:
                champ = profile.top_champions[i]
                key = keys.get(champ.champion_id)
                pixmap = icon_lookup(key) if key else None
                if pixmap is not None and not pixmap.isNull():
                    icon_label.setPixmap(pixmap)
                    icon_label.setToolTip(
                        names.get(champ.champion_id, f"#{champ.champion_id}")
                    )
                    icon_label.show()
                    continue
            icon_label.clear()
            icon_label.hide()

        # Fallback for stats text + when no icons available, fall back
        # to the old "Mains: A, B, C" format so the data isn't lost.
        bits: list[str] = []
        # Main role + winrate ON THAT ROLE first — most useful for the
        # player ("is this enemy comfortable in their assigned lane?").
        # Falls through to the legacy overall-WR display when no
        # role data is available.
        main_role = profile.main_role
        if main_role is not None:
            role_summary = profile.role_summary(main_role)
            if role_summary is not None:
                bits.append(f"{main_role} {role_summary}")
        else:
            wr = profile.win_rate
            total = profile.wins + profile.losses
            if total > 0 and wr is not None:
                bits.append(f"{int(wr * 100)}% WR ({total})")
        if profile.streak >= 3:
            bits.append(f"W{profile.streak} streak")
        elif profile.streak <= -3:
            bits.append(f"L{abs(profile.streak)} streak (tilt?)")
        # If we couldn't render any icons (no icon_lookup), surface
        # the mains as text so the data isn't lost.
        any_icon_visible = any(icon.isVisible() for icon in self._main_icons)
        if profile.top_champions and not any_icon_visible:
            tops = ", ".join(
                names.get(c.champion_id, f"#{c.champion_id}")
                for c in profile.top_champions[:3]
            )
            bits.insert(0, f"Mains: {tops}")
        if not bits:
            self._profile_label.setText(" ")
            return
        self._profile_label.setText(" · ".join(bits))

    def set_data(
        self,
        member: TeamMember,
        champion_name: str | None,
        counters: list[CounterEntry],
        icon: QPixmap | None = None,
        resolved_role: str = "",
        role_overridden: bool = False,
        damage_profile: str = "",
    ) -> None:
        self._cell_id = member.cell_id
        if member.champion_id == 0:
            self._champion_label.setText(self.PLACEHOLDER)
            self._icon_label.clear()
        else:
            self._champion_label.setText(champion_name or f"Champion #{member.champion_id}")
            if icon is not None and not icon.isNull():
                self._icon_label.setPixmap(icon)
            else:
                self._icon_label.clear()

        self._update_role_button_style(role=resolved_role, overridden=role_overridden)
        self._update_damage_badge(damage_profile)

        if counters:
            top = counters[:3]
            text = "Counters: " + ", ".join(
                f"{c.champion} ({c.score:.1f})" for c in top
            )
            self._counters_label.setText(text)
        else:
            self._counters_label.setText("")

    # -- Role button -----------------------------------------------------

    def _update_damage_badge(self, profile: str) -> None:
        """Render the AP/AD pill. Color-coded so the user can read it
        without parsing the letters: AP = magic-purple, AD = warm-red,
        AP/AD hybrid = neutral. Hidden when the profile is empty
        (un-picked or untaggable champion)."""
        if not profile:
            self._damage_badge.setText("")
            self._damage_badge.hide()
            return
        color = {
            "AP":    styles.AP_COLOR if hasattr(styles, "AP_COLOR") else styles.ACCENT,
            "AD":    styles.DANGER,
            "AP/AD": styles.WARNING,
        }.get(profile, styles.TEXT_MUTED)
        self._damage_badge.setText(profile)
        self._damage_badge.setStyleSheet(
            f"color: white;"
            f" background-color: {color};"
            f" font-size: {styles.FS_CAPTION}px; font-weight: 700;"
            f" padding: 1px 6px; border-radius: {styles.RADIUS_SMALL}px;"
            " letter-spacing: 0.4px;"
        )
        self._damage_badge.show()

    def _on_role_clicked(self) -> None:
        if self._cell_id >= 0:
            self.role_clicked.emit(self._cell_id)

    def _update_role_button_style(self, *, role: str, overridden: bool) -> None:
        text = role or "Auto"
        # Asterisk prefix marks a manual override so the user can tell at a
        # glance which slots they've adjusted.
        display = f"★ {text}" if overridden else text
        self._role_button.setText(display)
        color = styles.ACCENT if overridden else styles.TEXT_MUTED
        self._role_button.setStyleSheet(
            f"QPushButton {{ color: {color}; "
            f"background: transparent; border: none; padding: 2px 6px; "
            f"font-size: {styles.FS_LABEL}px; }}"
            f"QPushButton:hover {{ color: {styles.TEXT_PRIMARY}; }}"
        )

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

        # Optional profiling line — kept always-visible with empty text
        # so async profile data arriving doesn't cause a layout shift
        # (P5: no sudden size changes). When no data, the label still
        # reserves a single line of vertical space.
        self._profile_label = QLabel(" ")  # nbsp keeps height = 1 line
        self._profile_label.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY}; font-size: {styles.FS_CAPTION}px;"
            f" min-height: {styles.FS_CAPTION + 4}px;"
        )
        self._profile_label.setWordWrap(False)
        self._profile_label.setMinimumHeight(styles.FS_CAPTION + 4)

        outer.addLayout(head)
        outer.addWidget(self._counters_label)
        outer.addWidget(self._profile_label)

        self.clear()

    def clear(self) -> None:
        self._cell_id = -1
        self._champion_label.setText(self.PLACEHOLDER)
        self._update_role_button_style(role="", overridden=False)
        self._counters_label.setText("")
        self._icon_label.clear()
        self._profile_label.setText(" ")  # keep reserved space
        self._rank_pill.hide()

    def set_profile(self, profile: EnemyProfile | None, *,
                    champion_names: dict[int, str] | None = None) -> None:
        """Render an optional pre-game profile.

        Layout:
          rank pill (in the header, top-right corner)
          stats line below counters: Mains · WR · streak

        The profile label always reserves one line of vertical space so
        async data arrival doesn't trigger a layout shift (P5).
        """
        if profile is None or not profile.has_data:
            self._profile_label.setText(" ")  # keep reserved space
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

        names = champion_names or {}
        bits: list[str] = []
        if profile.top_champions:
            tops = ", ".join(
                names.get(c.champion_id, f"#{c.champion_id}")
                for c in profile.top_champions[:3]
            )
            bits.append(f"Mains: {tops}")
        wr = profile.win_rate
        total = profile.wins + profile.losses
        if total > 0 and wr is not None:
            bits.append(f"{int(wr * 100)}% WR ({total})")
        if profile.streak >= 3:
            bits.append(f"W{profile.streak} streak")
        elif profile.streak <= -3:
            bits.append(f"L{abs(profile.streak)} streak (tilt?)")
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

        if counters:
            top = counters[:3]
            text = "Counters: " + ", ".join(
                f"{c.champion} ({c.score:.1f})" for c in top
            )
            self._counters_label.setText(text)
        else:
            self._counters_label.setText("")

    # -- Role button -----------------------------------------------------

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
            f"font-size: 11px; }}"
            f"QPushButton:hover {{ color: {styles.TEXT_PRIMARY}; }}"
        )

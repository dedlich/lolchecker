"""Floating pre-game lobby stats widget — 5 enemy rows you can park
next to LeagueClient's champ-select view.

Driven by ``SessionView`` from the LCU pipeline (not LCDA — this is the
champ-select phase, before any game has started). Auto-shows when a
champ-select session is active, auto-hides between drafts.

Each row shows:
  [portrait] champion / summoner name           [rank-badge]
  Mains: A, B, C · 73% WR (15) · W3 streak
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..profiling.profile import EnemyProfile
from . import styles
from .badges import RankPill
from .floating_widget import FloatingWidget

PORTRAIT_SIZE = 32


class _LobbyRow(QFrame):
    """One enemy slot — portrait + name + rank + stats line."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "row")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(8)

        self._portrait = QLabel()
        self._portrait.setFixedSize(PORTRAIT_SIZE, PORTRAIT_SIZE)
        self._portrait.setScaledContents(True)
        self._portrait.setStyleSheet(
            f"background-color: {styles.BG_PRIMARY};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" border: 1px solid {styles.BORDER};"
        )
        head.addWidget(self._portrait)

        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        name_col.setContentsMargins(0, 0, 0, 0)
        self._champion = QLabel("")
        self._champion.setStyleSheet("font-weight: 600; font-size: 12px;")
        name_col.addWidget(self._champion)
        self._summoner = QLabel("")
        self._summoner.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 10px;")
        name_col.addWidget(self._summoner)
        head.addLayout(name_col, 1)

        self._rank_pill = RankPill()
        self._rank_pill.hide()
        head.addWidget(self._rank_pill)

        outer.addLayout(head)

        self._stats = QLabel("")
        self._stats.setStyleSheet(f"color: {styles.TEXT_SECONDARY}; font-size: 10px;")
        self._stats.setWordWrap(True)
        outer.addWidget(self._stats)

    def set_data(
        self,
        *,
        portrait: QPixmap | None,
        champion_name: str,
        summoner_name: str,
        profile: EnemyProfile | None,
        champion_names: dict[int, str],
    ) -> None:
        if portrait is not None and not portrait.isNull():
            self._portrait.setPixmap(portrait)
        else:
            self._portrait.clear()

        self._champion.setText(champion_name or "—")
        self._summoner.setText(summoner_name or "")

        if profile is None:
            self._rank_pill.hide()
            self._stats.setText("")
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

        bits: list[str] = []
        if profile.top_champions:
            tops = ", ".join(
                champion_names.get(c.champion_id, f"#{c.champion_id}")
                for c in profile.top_champions[:3]
            )
            bits.append(f"Mains: {tops}")
        wr = profile.win_rate
        total = profile.wins + profile.losses
        if total and wr is not None:
            bits.append(f"{int(wr * 100)}% WR ({total})")
        if profile.streak >= 3:
            bits.append(f"W{profile.streak}")
        elif profile.streak <= -3:
            bits.append(f"L{abs(profile.streak)} (tilt?)")
        self._stats.setText(" · ".join(bits))

    def clear(self) -> None:
        self._portrait.clear()
        self._champion.setText("")
        self._summoner.setText("")
        self._rank.setText("")
        self._stats.setText("")


class LobbyStatsWidget(FloatingWidget):
    KEY = "lobby_stats"
    DEFAULT_POS = (40, 220)
    DEFAULT_SIZE = (320, 380)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame[panel='true'] {{"
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f"  stop:0 rgba(20, 26, 34, 195), stop:1 rgba(10, 14, 20, 195));"
            f" border: 1px solid rgba(60, 70, 85, 220);"
            f" border-radius: {styles.RADIUS}px; }}"
            f" QFrame[role='row'] {{"
            f"  background-color: rgba(45, 55, 70, 130);"
            f"  border-radius: {styles.RADIUS_SMALL}px; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(4)

        title = QLabel("Lobby — Enemies")
        title.setObjectName("sectionTitle")
        outer.addWidget(title)

        self._champion_icons: dict[str, QPixmap] = {}
        self._rows: list[_LobbyRow] = []
        for _ in range(5):
            row = _LobbyRow()
            self._rows.append(row)
            outer.addWidget(row)

        outer.addStretch(1)
        self.hide()

    # -- public API -------------------------------------------------------

    def set_champion_icons(self, icons: dict[str, QPixmap]) -> None:
        self._champion_icons.update(icons)

    def update_view(self, view) -> None:  # type: ignore[no-untyped-def]
        """Forward the latest SessionView. Hides when there's no session
        OR connection isn't live."""
        if (
            view is None
            or view.session is None
            or view.connection_state != "connected"
            or not view.session.their_team
        ):
            self.hide()
            return

        self.fade_appear()
        their_team = view.session.their_team
        for i, row in enumerate(self._rows):
            if i >= len(their_team):
                row.hide()
                continue
            row.show()
            member = their_team[i]
            champ_id = member.champion_id
            champ_name = view.enemy_names.get(champ_id, "—") if champ_id else "—"
            champ_key = view.enemy_keys.get(champ_id, "") if champ_id else ""
            portrait = self._champion_icons.get(champ_key) if champ_key else None
            profile = view.enemy_profiles.get(member.cell_id) if view.enemy_profiles else None
            # Summoner name isn't in TeamMember (LCU strips it for privacy
            # depending on visibility), but the profile carries it.
            summoner_name = profile.summoner_name if profile else ""
            row.set_data(
                portrait=portrait,
                champion_name=champ_name,
                summoner_name=summoner_name,
                profile=profile,
                champion_names=view.enemy_names,
            )

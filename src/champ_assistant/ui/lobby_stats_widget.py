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
        self._champion.setStyleSheet(f"font-weight: 600; font-size: {styles.FS_BODY}px;")
        name_col.addWidget(self._champion)
        self._summoner = QLabel("")
        self._summoner.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_CAPTION}px;"
        )
        name_col.addWidget(self._summoner)
        head.addLayout(name_col, 1)

        self._rank_pill = RankPill()
        self._rank_pill.hide()
        head.addWidget(self._rank_pill)

        outer.addLayout(head)

        self._stats = QLabel("")
        self._stats.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY}; font-size: {styles.FS_CAPTION}px;"
        )
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
    """Loading-screen lobby panel: portrait + summoner + rank + last-10
    record + top mains for every visible player.

    Two stacked sections: ALLIES (5 rows, top) + ENEMIES (5 rows,
    bottom). The local player's row stays empty in the ally section
    since fetching your own profile is wasted API budget.

    Visibility: shown whenever a champ-select session is connected.
    The data fills in over the first ~10s of the session as Riot API
    responses land — late frames don't break, they just have empty
    rows that populate as fetches complete.
    """
    KEY = "lobby_stats"
    DEFAULT_POS = (40, 80)
    # Bigger default — 10 rows + 2 section titles + spacing.
    DEFAULT_SIZE = (340, 720)

    def __init__(self) -> None:
        super().__init__()
        # Shared floating-panel base + a slightly different row color
        # treatment unique to this widget's player rows.
        self.setStyleSheet(
            styles.floating_panel_stylesheet()
            + " QFrame[role='row'] {"
            f"  background-color: rgba(45, 55, 70, 130);"
            f"  border-radius: {styles.RADIUS_SMALL}px;"
            " }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(4)

        # Ally section (top)
        ally_title = QLabel("Allies")
        ally_title.setObjectName("sectionTitle")
        outer.addWidget(ally_title)

        self._champion_icons: dict[str, QPixmap] = {}
        self._ally_rows: list[_LobbyRow] = []
        for _ in range(5):
            row = _LobbyRow()
            self._ally_rows.append(row)
            outer.addWidget(row)

        # Enemy section (bottom) — re-uses the existing rows + render
        # path so adding the second team doesn't duplicate _LobbyRow
        # state-handling logic.
        enemy_title = QLabel("Enemies")
        enemy_title.setObjectName("sectionTitle")
        # Spacing above the enemy title so the two sections read as
        # distinct without needing a hard divider.
        enemy_title.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; text-transform: uppercase;"
            " letter-spacing: 1.2px; padding: 8px 0 2px 0;"
        )
        outer.addWidget(enemy_title)

        self._enemy_rows: list[_LobbyRow] = []
        for _ in range(5):
            row = _LobbyRow()
            self._enemy_rows.append(row)
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

        # Allies — local player's cell shows their own pick but no
        # profile (we don't fetch our own).
        my_team = view.session.my_team
        local_cell = view.session.local_player_cell_id
        for i, row in enumerate(self._ally_rows):
            if i >= len(my_team):
                row.hide()
                continue
            row.show()
            member = my_team[i]
            self._populate_row(
                row, member, view,
                profiles=view.ally_profiles,
                is_local=(member.cell_id == local_cell),
            )

        # Enemies — same render pipeline, different profile source.
        their_team = view.session.their_team
        for i, row in enumerate(self._enemy_rows):
            if i >= len(their_team):
                row.hide()
                continue
            row.show()
            member = their_team[i]
            self._populate_row(
                row, member, view,
                profiles=view.enemy_profiles,
                is_local=False,
            )

    def _populate_row(
        self,
        row: "_LobbyRow",
        member,  # type: ignore[no-untyped-def]
        view,    # type: ignore[no-untyped-def]
        *,
        profiles: dict,
        is_local: bool,
    ) -> None:
        """Single shared row-render path for both teams."""
        champ_id = member.champion_id
        champ_name = view.enemy_names.get(champ_id, "—") if champ_id else "—"
        champ_key = view.enemy_keys.get(champ_id, "") if champ_id else ""
        portrait = self._champion_icons.get(champ_key) if champ_key else None
        profile = profiles.get(member.cell_id) if profiles else None
        # Summoner name isn't in TeamMember (LCU strips it for privacy
        # depending on visibility), but the profile carries it.
        # The local player has no profile fetch — show "(you)" as a
        # placeholder so the row isn't visually empty.
        if is_local:
            summoner_name = "(you)"
        else:
            summoner_name = profile.summoner_name if profile else ""
        row.set_data(
            portrait=portrait,
            champion_name=champ_name,
            summoner_name=summoner_name,
            profile=profile,
            champion_names=view.enemy_names,
        )

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

        # Per-role winrate breakdown — one line, only roles with data,
        # sorted descending by games played. Empty string when no role
        # data yet so the row's height stays stable.
        self._roles = QLabel("")
        self._roles.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_CAPTION}px;"
            f" font-family: {styles.FONT_MONO};"
        )
        self._roles.setWordWrap(True)
        outer.addWidget(self._roles)

        # Behavior-tag pills row — small colored chips (OTP, Hot, Tilt,
        # Champ-Spec, Veteran, Newbie). Empty layout when no tags so
        # the row collapses cleanly.
        self._tags_row = QHBoxLayout()
        self._tags_row.setSpacing(4)
        self._tags_row.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._tags_row)

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
        # Streak rendering moves into the tag row when severe (≥4) —
        # the legacy ≥3 hint here only fires for the in-between band.
        if profile.streak == 3:
            bits.append("W3")
        elif profile.streak == -3:
            bits.append("L3")
        self._stats.setText(" · ".join(bits))

        # Per-role winrate line — compact, sorted by games played desc.
        if profile.role_winrates:
            ordered = sorted(
                profile.role_winrates.items(),
                key=lambda kv: kv[1][0] + kv[1][1],
                reverse=True,
            )
            parts = []
            for role, (w, l) in ordered:
                games = w + l
                if games == 0:
                    continue
                parts.append(f"{role} {int(100 * w / games)}%/{games}g")
            self._roles.setText(" · ".join(parts))
        else:
            self._roles.setText("")

        # Behavior tags as small pills.
        self._refill_tags(profile.behavior_tags)

    def _refill_tags(self, tags: list[str]) -> None:
        """Tear down + rebuild the small pill chips. Cheap — at most
        5-6 tags, max 6 QLabel instances per row."""
        while self._tags_row.count():
            item = self._tags_row.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for tag in tags:
            chip = QLabel(tag)
            chip.setStyleSheet(_tag_chip_stylesheet(tag))
            self._tags_row.addWidget(chip)
        self._tags_row.addStretch(1)

    def clear(self) -> None:
        self._portrait.clear()
        self._champion.setText("")
        self._summoner.setText("")
        self._stats.setText("")
        self._roles.setText("")
        self._refill_tags([])
        self._rank_pill.hide()


def _tag_chip_stylesheet(tag: str) -> str:
    """Pick a chip color based on tag semantics. Tilt = red, hot = green,
    OTP = accent purple, others neutral. Sticks to design tokens."""
    if tag.startswith("Hot"):
        color = styles.SUCCESS
    elif tag.startswith("Tilt"):
        color = styles.DANGER
    elif tag.startswith("OTP"):
        color = styles.ACCENT
    elif tag == "Autofill?":
        color = styles.WARNING
    elif tag == "Champ-Spec":
        color = styles.TIER_S
    else:
        color = styles.TEXT_MUTED
    return (
        f"color: white;"
        f" background-color: {color};"
        f" font-size: {styles.FS_CAPTION}px; font-weight: 700;"
        f" padding: 2px 8px; border-radius: {styles.RADIUS_PILL}px;"
        " letter-spacing: 0.4px;"
    )


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
            # Global champion-name map so the "Mains:" line can
            # resolve any champion the player has mastery on, not
            # just members of the current lobby.
            champion_names=view.all_champion_names or view.enemy_names,
        )

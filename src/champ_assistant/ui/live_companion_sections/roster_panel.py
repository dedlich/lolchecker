"""Roster panel — both teams' pre-game profile data.

Surfaces during the FINALIZATION / loading-screen window when all 10
players are locked in but the game hasn't started. Closes the b53fa9e
feature ask ("beim ladescreen soll dann die infos zu allen spielern
kommen") that went dormant when LobbyStatsWidget was retired in
v1.10.80. Restored as part of LiveCompanion in v1.10.103.

Each row is one player, single line:

    [portrait] Name        Mains: A · B · C    73% WR · W3

Hidden during BAN_PICK / planning to keep the active-draft layout
clean — the user is busy picking, not reading rank pills.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import styles

if TYPE_CHECKING:
    from ...data.models import TeamMember
    from ...profiling.profile import EnemyProfile
    from ..view_model import SessionView

IconLookup = Callable[[str], "QPixmap | None"]
NameLookup = Callable[[int], str]
KeyLookup = Callable[[int], str]

PORTRAIT_PX = 40
MAINS_ICON_PX = 16
MAX_MAINS_ICONS = 3
NAME_COL_PX = 160
STATS_COL_PX = 150


class _RosterRow(QFrame):
    """One player, single horizontal line. Hidden by default — only
    populated when the roster panel resolves a champion + profile
    pair into the slot."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("card", True)
        # Two-line grid: portrait spans both rows on the left, name +
        # mains stack in the middle column, stats span both rows on the
        # right. Fixed widths on the name and stats columns prevent the
        # long ``GameName#TagLine`` strings from pushing the stats label
        # off the edge of the row (v1.10.139 user report: "names overlap,
        # only 1 WR shows").
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 6, 12, 6)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)

        self._portrait = QLabel()
        self._portrait.setFixedSize(PORTRAIT_PX, PORTRAIT_PX)
        self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._portrait.setStyleSheet(
            f"background-color: {styles.BG_PRIMARY};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" border: 1px solid {styles.BORDER};"
        )
        grid.addWidget(self._portrait, 0, 0, 2, 1)  # rowspan 2

        self._name = QLabel("—")
        self._name.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_BODY}px; font-weight: 600;"
        )
        self._name.setFixedWidth(NAME_COL_PX)
        grid.addWidget(self._name, 0, 1)

        # Mains: up to 3 small champion icons. Fallback to text when
        # the icon prefetch hasn't caught a champion yet (rare).
        self._mains_layout = QHBoxLayout()
        self._mains_layout.setContentsMargins(0, 0, 0, 0)
        self._mains_layout.setSpacing(2)
        self._mains_text = QLabel("")
        self._mains_text.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
        )
        mains_container = QWidget()
        mains_container.setFixedWidth(NAME_COL_PX)
        mains_box = QHBoxLayout(mains_container)
        mains_box.setContentsMargins(0, 0, 0, 0)
        mains_box.setSpacing(4)
        mains_box.addLayout(self._mains_layout)
        mains_box.addWidget(self._mains_text, 1)
        grid.addWidget(mains_container, 1, 1)

        # Stats: "73% WR · W3" or empty when no profile data.
        self._stats = QLabel("")
        self._stats.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._stats.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_LABEL}px;"
        )
        self._stats.setFixedWidth(STATS_COL_PX)
        grid.addWidget(self._stats, 0, 2, 2, 1)  # rowspan 2

        grid.setColumnStretch(1, 1)

    def populate(
        self,
        *,
        member: "TeamMember",
        champion_key: str,
        profile: "EnemyProfile | None",
        icon_lookup: IconLookup,
        champion_keys: dict[int, str],
    ) -> None:
        """Fill the row from a single team member + their fetched profile.

        ``champion_key`` is the locked champion's string key (e.g.
        ``"Garen"``) — empty string before lock-in. ``profile`` may be
        None when no Riot key is configured or the fetch hasn't landed.
        """
        # Portrait — locked champion icon, fallback to first letter.
        if champion_key:
            pix = icon_lookup(champion_key)
            if pix is not None and not pix.isNull():
                self._portrait.setPixmap(pix.scaled(
                    PORTRAIT_PX, PORTRAIT_PX,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                self._portrait.setText("")
            else:
                self._portrait.setPixmap(QPixmap())
                self._portrait.setText(champion_key[:1].upper())
        else:
            self._portrait.setPixmap(QPixmap())
            self._portrait.setText("?")

        # Display name: profile-fetched summoner name, fallback to the
        # locked champion key when no profile data has landed yet.
        # TeamMember itself doesn't carry the display name — only the
        # opaque summoner_id / puuid — so the fallback after that is
        # the champion key.
        display_name = ""
        if profile is not None and profile.summoner_name:
            display_name = profile.summoner_name
        elif champion_key:
            display_name = champion_key
        if display_name:
            metrics = QFontMetrics(self._name.font())
            display_name = metrics.elidedText(
                display_name, Qt.TextElideMode.ElideRight, NAME_COL_PX - 4,
            )
        self._name.setText(display_name or "—")

        # Mains: up to 3 small icons OR text fallback. ``top_champions``
        # is already mastery-sorted descending in profile fetching.
        self._clear_mains()
        mains_keys: list[str] = []
        if profile is not None and profile.top_champions:
            for top_main in profile.top_champions[:MAX_MAINS_ICONS]:
                key = champion_keys.get(top_main.champion_id, "")
                if key:
                    mains_keys.append(key)

        added_icons = 0
        for key in mains_keys:
            pix = icon_lookup(key)
            if pix is not None and not pix.isNull():
                lbl = QLabel()
                lbl.setFixedSize(MAINS_ICON_PX, MAINS_ICON_PX)
                lbl.setPixmap(pix.scaled(
                    MAINS_ICON_PX, MAINS_ICON_PX,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                lbl.setToolTip(key)
                self._mains_layout.addWidget(lbl)
                added_icons += 1

        if added_icons == 0 and mains_keys:
            # Icon prefetch hasn't caught these champs — fall back to
            # text so the data isn't lost.
            self._mains_text.setText(" · ".join(mains_keys))
        elif added_icons > 0:
            self._mains_text.setText("")
        else:
            self._mains_text.setText("Mains: —")

        # Stats — last-10 winrate · current streak. Last-10 reads as
        # "current form" (loading-screen glanceable), more useful than
        # the longer 20-game window for the user's question of "is this
        # opponent hot or cold right now".
        if profile is not None and profile.has_data:
            stats_bits: list[str] = []
            l10 = profile.last_10_win_rate
            if l10 is not None:
                wr = int(round(l10 * 100))
                stats_bits.append(
                    f"{wr}% L10 ({profile.last_10_wins}W/"
                    f"{profile.last_10_losses}L)"
                )
            elif profile.win_rate is not None:
                # Fallback to the 20-game window when we don't have
                # a populated last-10 slice (e.g. legacy fixtures).
                wr = int(round(profile.win_rate * 100))
                stats_bits.append(f"{wr}% WR")
            if profile.streak:
                marker = "W" if profile.streak > 0 else "L"
                stats_bits.append(f"{marker}{abs(profile.streak)}")
            self._stats.setText(" · ".join(stats_bits))
        else:
            self._stats.setText("")

    def clear(self) -> None:
        self._portrait.setPixmap(QPixmap())
        self._portrait.setText("?")
        self._name.setText("—")
        self._stats.setText("")
        self._clear_mains()
        self._mains_text.setText("")

    def _clear_mains(self) -> None:
        while self._mains_layout.count():
            item = self._mains_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()


class RosterPanel(QWidget):
    """Two-team roster strip shown during finalization / loading.

    Five ally rows on top, a "vs" divider, five enemy rows below.
    Visibility is owned by the parent — call ``setVisible(False)``
    during BAN_PICK / planning.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(styles.SPACING_TIGHT)

        layout.addWidget(self._team_label("Ally team"))
        self._ally_rows: list[_RosterRow] = []
        for _ in range(5):
            row = _RosterRow()
            self._ally_rows.append(row)
            layout.addWidget(row)

        layout.addWidget(self._team_label("Enemy team"))
        self._enemy_rows: list[_RosterRow] = []
        for _ in range(5):
            row = _RosterRow()
            self._enemy_rows.append(row)
            layout.addWidget(row)

    @staticmethod
    def _team_label(text: str) -> QLabel:
        # Uppercase via .upper() — Qt QSS doesn't support text-transform.
        label = QLabel(text.upper())
        label.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY};"
            f" font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; letter-spacing: 1.6px;"
            " padding-top: 4px;"
        )
        return label

    def update_panel(
        self,
        view: "SessionView",
        icon_lookup: IconLookup,
    ) -> None:
        session = view.session
        if session is None:
            for row in self._ally_rows + self._enemy_rows:
                row.clear()
            return

        ally_members = session.my_team[:5]
        enemy_members = session.their_team[:5]

        for i, row in enumerate(self._ally_rows):
            if i < len(ally_members):
                m = ally_members[i]
                key = view.enemy_keys.get(m.champion_id) or \
                    view.all_champion_keys.get(m.champion_id, "")
                profile = view.ally_profiles.get(m.cell_id)
                row.populate(
                    member=m,
                    champion_key=key,
                    profile=profile,
                    icon_lookup=icon_lookup,
                    champion_keys=view.all_champion_keys,
                )
            else:
                row.clear()

        for i, row in enumerate(self._enemy_rows):
            if i < len(enemy_members):
                m = enemy_members[i]
                key = view.enemy_keys.get(m.champion_id) or \
                    view.all_champion_keys.get(m.champion_id, "")
                profile = view.enemy_profiles.get(m.cell_id)
                row.populate(
                    member=m,
                    champion_key=key,
                    profile=profile,
                    icon_lookup=icon_lookup,
                    champion_keys=view.all_champion_keys,
                )
            else:
                row.clear()

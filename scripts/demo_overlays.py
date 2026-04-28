"""Standalone demo: show the three floating overlay widgets with fake data.

Loads a midgame LCDA fixture, builds an LcdaSnapshot, instantiates each
floating widget and pushes the snapshot in. Useful for visual smoke
testing without a live League client.

Run:
    python scripts/demo_overlays.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from champ_assistant.jungle_timeline import JungleTimelineEngine  # noqa: E402
from champ_assistant.lcda.source import LcdaSource  # noqa: E402
from champ_assistant.ui.lobby_stats_widget import LobbyStatsWidget  # noqa: E402
from champ_assistant.ui.minimap_timers_widget import MinimapTimersWidget  # noqa: E402
from champ_assistant.ui.scoreboard_widget import ScoreboardWidget  # noqa: E402
from champ_assistant.ui.styles import global_stylesheet  # noqa: E402


def main() -> int:
    fixture = ROOT / "tests" / "fixtures" / "lcda" / "allgamedata_midgame.json"
    data = json.loads(fixture.read_text())

    src = LcdaSource(MagicMock(), lambda *_: None)
    snapshot = src._snapshot_from(data)

    app = QApplication(sys.argv[:1])
    app.setStyleSheet(global_stylesheet())

    scoreboard = ScoreboardWidget()
    scoreboard.move(40, 40)
    scoreboard.update_snapshot(snapshot)
    scoreboard.show()

    engine = JungleTimelineEngine()
    engine.tick(snapshot.game_time, list(snapshot.raw_events))

    minimap = MinimapTimersWidget()
    minimap.move(40, 130)
    minimap.attach_engine(engine)
    minimap.update_snapshot(snapshot)
    minimap.show()

    lobby = LobbyStatsWidget()
    lobby.move(40, 220)
    lobby.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

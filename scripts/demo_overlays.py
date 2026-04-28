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

    # Three minimap widgets at different simulated game-times so all
    # three confidence bands are on screen at once for visual review.
    # 200s = early game (HIGH), 1500s = mid (MID band), 3000s = late (LOW).
    from champ_assistant.jungle_timeline import CampState as _CS, JUNGLE_CAMPS as _JC

    def _force_confidence(eng: JungleTimelineEngine, conf: float) -> None:
        """Inject states with a fixed confidence so the demo shows all
        three bands regardless of where decay would land. Demo-only —
        production never touches the engine internals like this."""
        original = eng.states
        def _states_with_forced_conf() -> dict[str, _CS]:
            out = {}
            for cid, st in original().items():
                out[cid] = _CS(
                    id=st.id, name=st.name, state=st.state,
                    next_spawn_at=st.next_spawn_at,
                    time_remaining=st.time_remaining,
                    confidence=conf,
                )
            return out
        eng.states = _states_with_forced_conf  # type: ignore[method-assign]

    minimaps = []
    for idx, (gt, conf) in enumerate([(200.0, 0.9), (1500.0, 0.6), (3000.0, 0.3)]):
        eng = JungleTimelineEngine()
        eng.tick(gt, list(snapshot.raw_events))
        _force_confidence(eng, conf)
        m = MinimapTimersWidget()
        m.move(40, 130 + idx * 95)
        m.attach_engine(eng)
        m.update_snapshot(snapshot)
        m.show()
        minimaps.append((eng, m))

    lobby = LobbyStatsWidget()
    lobby.move(40, 220)
    lobby.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

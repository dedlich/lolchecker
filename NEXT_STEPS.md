# Next Steps — Windows Live Testing Handover

Picking up where the macOS dev session left off. Three new B-pillar coaching
modules shipped + one round of UI polish. All 1131 unit/lint tests green;
nothing has been live-tested against a real LCDA stream yet.

## What's new this version

### UI polish (apply on first launch — visual checks)
- **Scoreboard overlay** uses `styles.TEAM_ORDER` / `styles.TEAM_CHAOS` (centralised Riot blue/red) instead of inline hex. Visual: gold-diff overlay should look identical, just sourced from tokens.
- **PowerSpikePanel** + `rule_power_spike` now pick the highest-priority spike when multiple cross at once (level 11 + first item → shows level 11, not item). Was showing the last spike in list order.
- **RecommendationPanel.set_focus_mode(False)** now actually restores hidden rows immediately. Was waiting for the next 2 s LCDA tick.

### B2 — Gank Window Detection (`lcda/gank_window.py`)
Enemy jungler MIA detection from `ChampionKill` event timestamps. Two tiers:
- **info** — jungler not seen for 60 s
- **warn** — jungler not seen for 90 s

Only fires:
- For lane roles (TOP / MIDDLE / BOTTOM)
- During laning phase (4:00 – 20:00)
- When the jungler is alive (respawn resets the MIA clock)

State (`_jungler_last_seen_gt`, `_jungler_was_alive`) lives in `LcdaSource`,
so this only works correctly across consecutive snapshots — restart the
client and the clock resets.

### B4 — Tilt / Death-Pattern Detection (`lcda/tilt.py`)
Five-tier ladder driven by the active player's death timestamps:
- `caution` (info) — single lane death
- `tilt` (warn) — 2 deaths in 90 s
- `re_engage` (alert) — 2 deaths in 60 s ("1-and-done")
- `spiral` (alert) — 3 deaths in 180 s OR 3 in 60 s
- Modifiers: `bounty_lost` (died on 3+ killstreak), `solo_death` (no ally involvement within ±5 s)

Suppression: spiral-tier tilt **drops** all offensive recs (`fight`,
`power_spike`, `gold_lead`, …) so the engine can't tell a feeding player
to fight.

### B5 — Recall-Window Coaching (`lcda/active_state.py` + `rule_recall_check`)
Reads HP %, mana %, current gold from `activePlayer.championStats`. Picks
one of four signals per tick:
1. Critical HP < 30 % → `recall_critical` (alert)
2. Resource depleted + ≥ 1100 g → `recall_resource` (warn)
3. Pure gold opportunity ≥ 1300 g → `recall_gold` (info)
4. Mana < 20 % in lane → `mana_check` (info)

Energy users (Akali / Lee Sin / Zed / Kennen / Shen) skip tier 4 — energy
regen is too fast to make "low mana" actionable.

## Live-test checklist (Windows)

Run a normal game and watch the floating recommendation panel. Verify:

### Sanity (smoke)
- [ ] No exceptions in `~/AppData/Local/ChampAssistant/logs/` after a full game
- [ ] All 1131 tests still pass on Windows: `pytest tests/unit tests/lint -q`
- [ ] Power spike panel still appears in the main overlay during champ-select

### B2 gank window
- [ ] Pick a lane role. After ~5:00 wait until the enemy jungler hasn't shown in any kill log entry → expect "<Jungler> seit 60 s nicht gesehen — Vorsicht in der Lane" (info)
- [ ] Same situation past 90 s → severity should escalate to warn
- [ ] When the jungler dies + respawns → MIA clock resets, no premature alert
- [ ] No alert fires before 4:00 or after 20:00
- [ ] No alert fires when playing JUNGLE / UTILITY yourself

### B4 tilt
- [ ] First death in lane (< 14:00) → "Erster Tod — …" (info)
- [ ] Two deaths in ~80 s → "Tilt-Fenster …" (warn)
- [ ] Two deaths in ~50 s → "1-AND-DONE …" (alert)
- [ ] After 3 deaths in 3 min → "DEATH SPIRAL …" (alert) AND no fight / power-spike recs visible
- [ ] If you died on a 3+ killstreak → "Bounty (3+ Streak) verloren" suffix appended
- [ ] If no ally died within ±5 s of you → "Alleine gestorben" suffix appended

### B5 recall window
- [ ] HP < 30 % anywhere on the map → red alert "RECALL JETZT, nächster Trade tötet dich"
- [ ] HP 40 % + gold > 1100 in lane → warn "Recall lohnt — HP 40% + …, 1100g für Component"
- [ ] Full HP + 1300+ gold in lane → info "1300g — Recall-Fenster …"
- [ ] Energy champion (Lee Sin / Akali / Zed) at 5 % energy → no rec (correctly skipped)
- [ ] Past 20:00 with anything but critical HP → no recall recs (suppressed by phase cutoff)

### Things that should NOT happen
- [ ] Tilt + power-spike recs simultaneously when in spiral tier (suppression bug if seen)
- [ ] Recall recommendation fires while the player is dead (hp_pct = 0)
- [ ] Gank alert fires for an active jungler (i.e., the user IS the jungler)

## Open work / known gaps

These are the next items from `memory/project_strategy_charter.md`:

- **A2 — Performance audit / event indexing** — engine has ~25 rules each scanning `raw_events`; pre-indexing by `EventName` in the snapshot constructor would cut redundant work. Not yet measured as a real bottleneck.
- **B3 — Dedicated objective-priority service** — currently spread across `rule_dragon_window` / `rule_baron_window` / `rule_herald_priority`. Could consolidate with a unified scoring model.
- **Splitting `decision_engine.py`** — 3700+ lines, 49 rules. Pure refactor, no behaviour change. Worth doing once rule-add cadence slows.
- **Vision / ward coaching** — not feasible from LCDA (no ward state in the event log). Would need vision subsystem extension.
- **Wave management** — same blocker; LCDA exposes per-player CS but not minion wave positions.

## Code map (where to look on Windows)

```
src/champ_assistant/lcda/
  active_state.py      # extract_active_combat_state, ActiveCombatState
  gank_window.py       # detect_gank_risk, GankAlert
  tilt.py              # detect_tilt, TiltState (5-tier ladder)
  source.py            # LcdaSource — wires all 3 into LcdaSnapshot
  power_spikes.py      # unchanged — pre-existing spike + enemy-spike detection

src/champ_assistant/advisor/decision_engine.py
  rule_recall_check         # B5 — line ~3050
  rule_tilt_detection       # B4 — line ~3140
  rule_gank_risk            # B2 — line ~3210
  _suppress_dominated       # spiral-tilt offensive-drop clause at end

tests/unit/
  test_active_state.py      # 27 tests
  test_gank_window.py       # 31 tests
  test_tilt.py              # 31 tests
```

## Quick commands

```bash
# Run all tests
.venv\Scripts\python -m pytest tests/unit tests/lint -q

# Run only the new modules' tests
.venv\Scripts\python -m pytest tests/unit/test_active_state.py tests/unit/test_gank_window.py tests/unit/test_tilt.py -v

# Run a live demo to see all rules render in the recommendation panel
.venv\Scripts\python -m champ_assistant --demo-recommendations
```

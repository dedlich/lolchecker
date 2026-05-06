# Decision Engine — Rule Catalog

The decision engine surfaces actionable coaching from raw LCDA snapshots. This
file is the navigable index of every rule, organized by category. Source of truth
is `src/champ_assistant/advisor/decision_engine/_rules.py`; this catalog lives
next to it for human readers.

**Engine size:** 53 rules in `ALL_RULES` + 3 spell-tracker rules (gated on
`spell_tracker` argument) + 1 situational build rule (gated on `situational_build`
argument) = **57 callable rules**.

## How to read

Each rule below shows:
- **Kind** — the `kind` string in the emitted `Recommendation` (used by the
  suppression layer for de-duplication).
- **Severity** — `info` / `warn` / `alert`. Tier-based rules list each tier.
- **Category** — `objective` / `tempo` / `safety` / `lane`. Drives the UI glyph
  (`◈` / `▶` / `✕` / `≡`).
- **Fires when** — one-line trigger condition.

Rules with **hysteresis** fire once per "episode" (per tier per life, per game,
etc.) instead of every 2-second LCDA tick. See `_state.py` for the singletons +
matching `reset_*_hysteresis` test fixtures.

---

## 1. Game lifecycle

### `rule_game_ended`
- **Kind:** `game_end` · **Severity:** `info` · **Category:** `tempo`
- **Fires when:** `GameEnd` event present in `raw_events`. Returns the
  Win/Loss summary card.
- **Suppression:** appears in `_suppress_dominated` Rule 0 — when `game_end`
  is in the rec set, **all other recs are dropped** so only the result card
  is rendered.

### `rule_ace_detected`
- **Kind:** `ace` · **Severity:** `alert` · **Category:** `tempo`
- **Fires when:** Active team has 5 alive, enemy team has 0 alive (or 4-0).
- **Notes:** Rule 1 of `_suppress_dominated` — `ace` drops 22 other kinds
  (fight, gold_lead, lane_mia, all bounty kinds, all objective_taken_*,
  teamfight_won*, etc.). Ace is the umbrella signal.

---

## 2. Personal coaching (active-player state)

### `rule_recall_check`
- **Kind:** `recall_critical` (alert) / `recall_resource` (warn) /
  `recall_gold` (info) / `mana_check` (info) · **Category:** `safety`
- **Tier ladder:**
  1. **Critical HP** (alert) — HP < 30 % regardless of phase or gold
  2. **Resource depleted + back-worth gold** (warn) — HP < 50 % OR mana < 30 %, AND gold ≥ 1100 g, in lane phase
  3. **Pure gold opportunity** (info) — gold ≥ 1300 g in lane phase
  4. **Mana check** (info) — mana < 20 % in lane phase, mana users only
- **Hysteresis:** `_RecallHysteresis` — each tier re-arms only after recovery
  past its rearm threshold. Reset on death.

### `rule_tilt_detection`
- **Kind:** `tilt` · **Severity:** info / warn / alert (tier-dependent)
- **Tier ladder (death-pattern based):**
  1. **caution** (info) — single lane death (game < 14:00)
  2. **tilt** (warn) — 2 deaths in 90 s
  3. **re_engage** (alert) — 2 deaths in 60 s, the "1-and-done" pattern
  4. **spiral** (alert) — 3 deaths in 180 s OR 3 in 60 s
- **Modifiers:** `bounty_lost` (died on 3+ killstreak), `solo_death` (no
  ally involvement within ±5 s).
- **Notes:** Rule 11 of `_suppress_dominated` — when severity == "alert"
  (spiral / re_engage), drops every offensive call.

### `rule_active_bounty`
- **Kind:** `active_bounty` · **Severity:** info (3-4 streak) / warn (5+)
- **Tiers:** Killing Spree (+150g) / UNSTOPPABLE (+300g) / GODLIKE (+500g),
  matching Riot's announcer thresholds.
- **Hysteresis:** `_BountyHysteresis` — once-per-tier-per-life; resets on death.

### `rule_unspent_skill_points`
- **Kind:** `skill_point_unspent` · **Severity:** `info` · **Category:** `lane`
- **Fires when:** `(player level − sum Q/W/E/R levels) ≥ 1` AND game_time ≥ 60 s
  AND HP ≥ 50 % AND alive.
- **Notes:** Suppressed below 50 % HP — micro-nag during a trade is harmful.

### `rule_matchup_mismatch`
- **Kind:** `matchup_mismatch` · **Severity:** info / warn (deficit-based)
- **Tier ladder (deficit = deaths_to_X − kills_on_X):**
  - deficit 2 → info "X tötet dich oft"
  - deficit 3+ → warn "X dominiert dich, Lane verloren"
- **Notes:** Per-enemy hysteresis. Distinguishes matchup loss from generic tilt.

---

## 3. Map awareness — "where is everyone?"

### `rule_gank_risk`
- **Kind:** `gank_risk` · **Severity:** info (60 s MIA) / warn (90 s MIA)
- **Fires when:** Enemy jungler hasn't appeared in `ChampionKill` events
  (as killer/victim/assister) for ≥ 60 s during laning phase
  (4:00 – 20:00) and is alive.
- **Phase guards:** Lane roles only (TOP/MIDDLE/BOTTOM). Dead → alive
  transition resets the MIA clock.

### `rule_lane_opponent_mia`
- **Kind:** `lane_mia` · **Severity:** info / warn
- **Fires when:** Active player's lane opponent (same `position`) hasn't
  gained CS for ≥ 30 s while alive (info) or ≥ 60 s (warn).
- **Notes:** Bot lane tracks the BOTTOM enemy (ADC) not UTILITY (support).
  JUNGLE / UTILITY active player is excluded.

### `rule_enemy_bounty`
- **Kind:** `enemy_bounty` · **Severity:** info / warn
- **Tiers:** 3-streak / 5-streak / 7+-streak on any enemy.
- **Hysteresis:** `_EnemyBountyHysteresis` — per-enemy fired-tier with death reset.

### `rule_ally_bounty`
- **Kind:** `ally_bounty` · **Severity:** info / warn
- **Tiers:** Same 3/5/7+ ladder applied to allies (active player excluded).
- **Position-aware advice:**
  - TOP: "TP-Engages für Top, Welle für Top freihalten"
  - JUNGLE: "Jungle wardēn (river + buffs), Counter-Gank-Pressure"
  - MIDDLE: "Mid-Roams unterstützen, Welle für Mid freihalten"
  - BOTTOM: "Bot-Side stacken (Drachen), Engages mit CC"
  - UTILITY: "Bot stacken, Engages koordinieren"

### `rule_shutdown_taken`
- **Kind:** `shutdown_taken` · **Severity:** info / warn / alert
- **Fires when:** A previously-bountied enemy (tier ≥ 3 while alive) just died.
  The complement to `rule_enemy_bounty`.
- **Mechanics:** Two-pass — pass 1 captures pre-death tier per enemy,
  pass 2 fires when the enemy is dead and `deaths > fired_for_death`.
  `_kill_streak` resets to 0 on death so capturing pre-death state is required.

---

## 4. Objectives — drake / baron / herald / void grubs / soul / elder

### `rule_dragon_window` (and related)
- **Kind:** `dragon_take` / `dragon_free` · **Severity:** alert / warn
- **Fires when:** Drake is up; takes context (numbers, gold-diff, vision proxy).

### `rule_baron_window`
- **Kind:** `baron_take` / `baron_free` · **Severity:** alert / warn
- **Fires when:** Baron is up; same context model as dragon.

### `rule_herald_priority`
- **Kind:** `herald_priority` · **Severity:** info
- **Fires when:** Herald available before 14:00.

### `rule_elder_window`
- **Kind:** `elder_take` · **Severity:** alert
- **Fires when:** Elder Dragon available, post-soul.

### `rule_dragon_soul_pressure`
- **Kind:** `dragon_soul` · **Severity:** info
- **Fires when:** Ally team at 3 drake stacks — soul is the next drake; reminder
  to set up.

### `rule_void_grubs`
- **Kinds:** `enemy_hornguard` (warn) / `ally_hornguard` (info) /
  `void_grub_contest` (info) — three distinct sub-states.
- **Fires when:** 4:30 – 14:00 window. Hornguard = team has 3+ grubs (Voidmite
  buff active for tower siege).

### `rule_objective_setup_window`
- **Kind:** `objective_setup` · **Severity:** `info`
- **Fires when:** Drake/Baron/Herald/VoidGrubs is 30 – 90 s from spawning.
  Position-aware advice (near-pit vs far-pit; JUNGLE always gets Smite cue).
- **Priority:** Baron > Dragon > Herald > VoidGrubs when multiple windows open.

### `rule_objective_taken_by_ally`
- **Kinds:** `objective_taken_baron` (alert) / `objective_taken_elder` (alert) /
  `objective_taken_soul` (alert) / `objective_taken_drake` (info) /
  `objective_taken_herald` (info)
- **Fires when:** Ally team killed the objective in the last 20 s. Phase-aware
  advice scales by objective type and game time.

### `rule_enemy_dragon_soul`
- **Kind:** `enemy_soul_point` · **Severity:** warn
- **Fires when:** Enemy team at 3 drakes — denial reminder.

### `rule_objective_bounty_active`
- **Kinds:** `objective_bounty_behind` / `objective_bounty_ahead` · info
- **Fires when:** `|gold_diff| ≥ 4500g` in mid-game (8:00 – 35:00). Two-sided
  framing — comeback bounty (behind) vs shutdown awareness (ahead).
- **Notes:** Suppressed by `far_behind_safe` (deep deficit → safe play wins).

---

## 5. Power spikes & early game

### `rule_power_spike`
- **Kind:** `power_spike` · **Severity:** alert (level 6) / warn (level 11/16
  or 2-item) / info (other)
- **Fires when:** Active player just crossed a level threshold (6/11/16) or
  item milestone (1/2/3 legendaries). Picks highest-priority spike when
  multiple cross simultaneously.

### `rule_enemy_item_spike`
- **Kind:** `enemy_spike` · **Severity:** info / warn (count-based)
- **Fires when:** Enemy player just completed a 1st/2nd/3rd legendary.

### `rule_first_blood`
- **Kind:** `first_blood` · **Severity:** info (ally / active got it) /
  warn (enemy got it)
- **Fires when:** First `ChampionKill` event in `raw_events`. Single-fire per game.

### `rule_plate_window`
- **Kind:** `plate_window` · **Severity:** `info`
- **Fires when:** game_time ∈ [13:00, 14:00) — the last-call window before
  outer-turret plates despawn at 14:00. Single-fire per game.

### `rule_fight_window_closing`
- **Kind:** `window_closing` · **Severity:** alert
- **Fires when:** Enemy is dead but about to respawn within 12 s — finish the push.

---

## 6. Numbers / fight asymmetry

### `rule_numbers_advantage`
- **Kind:** `numbers_adv` · **Severity:** info / warn
- **Fires when:** More allies alive than enemies; suggests pressing tempo.

### `rule_numbers_disadvantage`
- **Kind:** `numbers_disadv` · **Severity:** warn / alert
- **Fires when:** More enemies alive than allies; "play safe" overrides
  many offensive calls. **The umbrella safety signal** — Rule 2 of
  `_suppress_dominated` drops 26 offensive kinds when `numbers_disadv`
  is present.

### `rule_fight_opportunity`
- **Kind:** `fight` (alert) / `fight_bad` (warn)
- **Fires when:** `fight_score(snapshot)` exceeds threshold; encodes a heuristic
  combat-readiness model (gold + level + numbers).
- **Notes:** Includes focus-target call ("Fokus Jinx") and AoE-CC warnings
  ("ACHTUNG: Orianna — NICHT CLUSTERN!").

### `rule_teamfight_outcome`
- **Kinds:** `teamfight_won` (info) / `teamfight_won_big` (alert) /
  `teamfight_lost` (warn) / `teamfight_lost_big` (alert)
- **Fires when:** ≥ 3 ChampionKills in the last 15 s + |net| ≥ 2.
- **Hysteresis:** `_TeamfightOutcomeHysteresis` — once per fight (keyed on
  latest event time).
- **Asymmetric suppression:** `teamfight_lost*` survives `numbers_disadv`
  (it explains the disadvantage); `teamfight_won*` doesn't (contradicts).

---

## 7. Gold / level / CS macro

### `rule_gold_lead_push`
- **Kind:** `gold_lead` · **Severity:** info
- **Fires when:** items_value diff ≥ +3000g.

### `rule_far_behind_safe`
- **Kind:** `far_behind_safe` · **Severity:** warn
- **Fires when:** items_value diff ≤ -5000g.
- **Notes:** Suppresses `objective_bounty_behind` — at deep deficits scaling
  beats forcing comeback objectives.

### `rule_kill_lead_snowball`
- **Kind:** `kill_lead` · **Severity:** info
- **Fires when:** Team kill-diff ≥ +5; press the snowball with tempo.

### `rule_kill_deficit_defensive`
- **Kind:** `kill_deficit` · **Severity:** warn
- **Fires when:** Team kill-diff ≤ -7; bunker at the inhib, no extending.

### `rule_level_deficit`
- **Kind:** `level_deficit` · **Severity:** warn
- **Fires when:** Average level diff ≤ -1.5; fair fights are auto-losses.

### `rule_cs_deficit`
- **Kind:** `cs_deficit` · **Severity:** info / warn
- **Fires when:** CS/min < expected (8.0/min) by ≥ 2.0 (info) or ≥ 3.5 (warn).
  Lane roles only (excludes JUNGLE / UTILITY).
- **Game-time guard:** active in 4:00 – 28:00 window only.

### `rule_lane_level_advantage`
- **Kind:** `lane_level_adv` · **Severity:** info / warn
- **Fires when:** Active player is +2 levels ahead of (or behind) lane opponent
  during laning phase (< 20:00).

### `rule_late_game_group`
- **Kind:** `late_game_group` · **Severity:** info
- **Fires when:** game_time > 30:00 — the "no splits, group 5, every death is
  50s+" reminder.

---

## 8. Structural — turrets / inhibs / base

### `rule_enemy_herald_danger`
- **Kind:** `enemy_herald` · **Severity:** info
- **Fires when:** Enemy team picked up Herald (3-min usage window) — defensive
  reminder for the side they'll likely send it down.

### `rule_ally_herald_window`
- **Kind:** `ally_herald` · **Severity:** info
- **Fires when:** Ally team picked up Herald — surfaces the placement window so
  the buff doesn't expire unused.

### `rule_enemy_inhibitor_down`
- **Kind:** `inhib_down` · **Severity:** alert
- **Fires when:** Ally team destroyed an enemy inhibitor. Time to push for nexus.

### `rule_enemy_inhib_expiring`
- **Kind:** `inhib_expiring` · **Severity:** warn
- **Fires when:** Enemy inhibitor will respawn within 60s.

### `rule_ally_inhib_down`
- **Kind:** `ally_inhib_down` · **Severity:** alert
- **Fires when:** Enemy team destroyed our inhibitor.
- **Notes:** **Umbrella signal** — Rule 8 of `_suppress_dominated` drops
  26 offensive/coaching kinds when present.

### `rule_ally_inhib_respawning`
- **Kind:** `ally_inhib_respawning` · **Severity:** info
- **Fires when:** Our inhibitor will respawn within 60s.
- **Notes:** Deliberately coexists with `ally_inhib_down` — "defend now +
  back in 30s" are complementary, not conflicting.

### `rule_ally_turret_lost`
- **Kind:** `ally_turret_lost` · **Severity:** info
- **Fires when:** Ally turret destroyed in last 60 s.

### `rule_enemy_base_exposed`
- **Kind:** `base_exposed` · **Severity:** alert
- **Fires when:** Enemy inner turret destroyed; nexus is reachable.

### `rule_lane_pressure`
- **Kind:** `lane_open` · **Severity:** info
- **Fires when:** Generic lane-pressure window (turret balance favors push).

### `rule_enemy_jungler_down`
- **Kind:** `jungler_down` · **Severity:** info
- **Fires when:** Enemy jungler dead with ≥ 5s respawn AND objective coming up.

---

## 9. Buff timers — baron / elder

### `rule_baron_buff_expiring`
- **Kind:** `baron_buff_expiring` · **Severity:** warn
- **Fires when:** Ally team's baron buff has < 60 s remaining; use the last
  seconds.

### `rule_enemy_baron_buff`
- **Kind:** `enemy_baron_buff` · **Severity:** warn
- **Fires when:** Enemy team has baron buff active.
- **Notes:** Survives `numbers_disadv` (the warning is more critical when
  short-handed).

### `rule_elder_buff_expiring`
- **Kind:** `elder_buff_expiring` · **Severity:** warn
- **Fires when:** Ally team's Elder buff has < 60 s remaining; force the
  closing-window play before execute drops.

### `rule_enemy_elder_buff`
- **Kind:** `enemy_elder_buff` · **Severity:** alert
- **Fires when:** Enemy team has Elder Dragon buff active.
- **Notes:** **Umbrella signal** — Rule 10 of `_suppress_dominated` drops
  11 fight kinds. Fighting through the execute is nearly always fatal.

---

## 10. Spell-tracker rules (require `spell_tracker` arg to `evaluate`)

### `rule_enemy_flash_down`
- **Kind:** `flash_down` · **Severity:** info
- **Fires when:** A tracked enemy's Flash is on cooldown for ≥ 60 s remaining.
  Engage window open.

### `rule_enemy_tp_down`
- **Kind:** `tp_down` · **Severity:** info
- **Fires when:** A tracked enemy's TP is on cooldown for ≥ 90 s remaining.
  Side-lane pressure window.

### `rule_enemy_combat_spell_down`
- **Kind:** `combat_spell_down` · **Severity:** info
- **Fires when:** Enemy ADC's Heal/Cleanse/Barrier/Exhaust/Ignite is on cooldown.

---

## 11. Build (gated on `situational_build` arg)

### `rule_situational_build`
- **Kind:** `situational_build` · **Severity:** info · **Category:** `lane`
- **Fires when:** game_time > 120 s AND build engine produced ≥ 1 situational
  item. Surfaces top-3 with reasons.

---

## Suppression matrix

The `_suppress_dominated` function in `_evaluate.py` runs after rule evaluation
and culls contradictory recs. **Umbrella signals** (left column) silence the
listed kinds on the right.

| Umbrella signal | Silences (sample) |
|---|---|
| **`game_end`** | All other recs |
| **`ace`** | `fight`, `fight_bad`, `numbers_adv`, `gold_lead`, `kill_lead`, `jungler_down`, `window_closing`, `gank_risk`, `tilt`, `recall_resource`, `recall_gold`, `mana_check`, `lane_mia`, `objective_setup`, `skill_point_unspent`, `active_bounty`, `enemy_bounty`, `ally_bounty`, `matchup_mismatch`, `plate_window`, `first_blood`, `teamfight_won*`, `shutdown_taken`, `objective_taken_*`, `objective_bounty_*` |
| **`numbers_disadv`** | All offensive recs (26 kinds) — `fight`, all objective takes, all bounty kinds (active/enemy/ally), `power_spike`, `cs_deficit`, `lane_level_adv`, `lane_mia`, `objective_setup`, `skill_point_unspent`, `matchup_mismatch`, `plate_window`, `first_blood`, `teamfight_won*`, `shutdown_taken`. **`teamfight_lost*` deliberately survives** — they explain the disadvantage. |
| **`ally_inhib_down`** | All push / coaching kinds (26 kinds) — every objective take, all bounty kinds, all `objective_taken_*`, `objective_bounty_*`. **`ally_inhib_respawning` deliberately coexists** — "defend now + back in 30s" are complementary. |
| **`enemy_elder_buff`** | 11 fight kinds — fighting through the execute is fatal. |
| **`far_behind_safe`** | `objective_bounty_behind` — at deep deficits scaling beats forcing comeback objectives. |
| **`tilt` (severity == alert)** | All offensive recs — `fight`, `numbers_adv`, `gold_lead`, `kill_lead`, `power_spike`, `lane_level_adv`, `lane_mia`, `objective_setup`, `skill_point_unspent`, `enemy_bounty`, `ally_bounty`, `matchup_mismatch`, `plate_window`, `teamfight_won*`, `shutdown_taken`, `objective_taken_*`, `objective_bounty_*`, `flash_down`, `tp_down`, `combat_spell_down`, `jungler_down`, `dragon_soul`. The spiral-tilt drop is the largest single-rule suppression. |

**Cross-objective priority:** when both `baron_take`/`baron_free` and
`dragon_take`/`dragon_free` fire, baron wins (Rule 9). When `inhib_down` fires,
`base_exposed` and `lane_open` are dropped (the state has advanced past them).

---

## Adding a new rule

1. Add the `def rule_*(snapshot)` function to `_rules.py`. Pure functions only —
   no I/O, no asyncio, no Qt.
2. Register it in `ALL_RULES` (also in `_rules.py`) under the right category
   comment.
3. If it carries cross-tick state, add a `_FooHysteresis` class +
   `reset_foo_hysteresis()` helper to `_state.py`. Tests import the reset
   helper via the `__init__` wildcard.
4. If the new `kind` should be silenced under any umbrella signal, add it
   to the right set in `_suppress_dominated()` (`_evaluate.py`).
5. Write tests in `tests/unit/test_*.py`. Use `autouse` fixtures to call any
   `reset_*_hysteresis` helpers your rule depends on.
6. Update this file with a one-paragraph entry under the right category.

The `time.perf_counter()` instrumentation in `evaluate()` records every rule's
duration to the `RuleTimingRecorder`, which flushes a digest to
`rule_timing.log` on shutdown. New rules show up automatically. The digest
also tracks per-rule **invocations** (lifetime call count) and **fires**
(times the rule produced a non-None Recommendation), with `fire_rate = fires /
invocations` in the output. A new rule that never fires AND has no
invocations is dead code; one that fires every tick may need throttling.

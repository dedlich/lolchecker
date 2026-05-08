# Session continuation — v1.10.99

Hand-off note for the next session. Read this first if you've been
dropped into this repo cold. Earlier handoff at v1.10.80 covers the
engine / __main__ / settings refactor arcs; v1.10.81 → v1.10.88
covered the LiveCompanion live-test polish round; this section
covers the v1.10.89 → v1.10.99 wire-bug audit cycle.

---

## Where we are (latest)

* **Tag**: `v1.10.99` (cut on macOS dev box, pushed to `origin/main`).
* **Suite**: 1666 / 1666 green via
  `.venv/bin/pytest tests/ -q --ignore=tests/soak`.
* **Platform**: User has been pulling fixes onto Windows between
  rounds and reporting back what holds up. v1.10.83-v1.10.88 fixes
  came from one such report; v1.10.89-v1.10.99 are mostly proactive
  audit findings of the same wire-bug class.

## v1.10.89 → v1.10.99: wire-bug audit cycle

The v1.10.85 / .87 / .88 fixes surfaced a recurring class — "wire
silently dropped on a refactor" — and the user asked to keep auditing
for more. Ten versions shipped this round, organised by sub-class:

### Stub data flowing through to UI (data layer never updated)

| Tag | Bug | Shape |
|---|---|---|
| 1.10.90 | Power Spikes bar always pure mid-game | `tags_lookup=lambda _key: []` stub at the SummaryRow call site → every champion fell into "no early/late tags" mid bucket. Pre-computed `(early, mid, late)` per side in view_builder, surfaced as `ally_phase_distribution` / `enemy_phase_distribution` |
| 1.10.91 | Pick suggestions never showed build adaptation reasons | PickCard rendered `view.suggestion_build_reasons`, but the LiveCompanion redesign replaced PickCard with a minimal `_ClickableRow` that dropped it. Wired into PicksColumn rows |

### Wasted resources (API quota / CPU / memory)

| Tag | Bug | Cost |
|---|---|---|
| 1.10.92 | Ally profile fetching wired but no UI consumer (LobbyStatsWidget retired in v1.10.80) | ~16 Riot API calls / champ-select burned on data nothing read |
| 1.10.95 | Telemetry `_pending` queue grew unbounded after `stop()` (caused by v1.10.93's runtime stop) | Slow memory leak when telemetry disabled mid-session — band-tracker still recorded |
| 1.10.97 | `_on_settings_changed` rebuilt ProfileService — old `httpx.AsyncClient` connection pool leaked every Save | ~100 sockets per Save, GC-eventual |

### Settings toggle ignored at runtime (long-running services / widgets)

| Tag | Bug | Fix |
|---|---|---|
| 1.10.93 | Telemetry + diagnostics gated only at startup | Wired both `start()` / `stop()` calls into `_on_settings_changed` |
| 1.10.94 | `focus_mode` toggle ignored | Wired `recommendation_panel.set_focus_mode(...)` into the handler |
| 1.10.96 | `show_summoners` / `show_spikes` panel toggles ignored | New `MainOverlay.apply_runtime_settings()` reloads `overlay_config` and routes the diff through the existing `_on_panel_toggled` |
| 1.10.98 | Vision services kept running after toggle-off | Same partial coverage — start/stop the constructed service per new flag |
| 1.10.99 | `show_scoreboard` / `show_minimap_timers` couldn't toggle on from boot-off | Construct-then-hide refactor: always construct, gate user visibility via `set_user_enabled` flag on each widget |

### Pattern recap

Three sub-classes, all the same shape: a Settings change persists to
disk but the running process doesn't re-read it. The clean fixes:

1. **Pre-compute in view_builder** rather than passing raw deps through
   to UI (avoids stub lookups in UI). v1.10.85, v1.10.90.
2. **In-place mutators** (`set_credentials`, `set_patch`,
   `set_user_enabled`) rather than rebuild-and-replay. v1.10.88,
   v1.10.93, v1.10.97, v1.10.99.
3. **Construct-then-hide** instead of conditional construction. v1.10.99
   landed it for floating widgets; vision services still need it.

## Live testing checklist (pull on Windows + open a champ-select)

These are the things to verify the v1.10.83+ fixes actually held up:

1. **Build pushed at lock-in shows a 3-block Meraki blueprint** (Starting / Build Order / Situational, ~13 items total) NOT a 4-item static set. Status bar should read `Apply Build {Champion}: Items aktiviert` (Meraki path) — if it says `Items (Fallback)` then Meraki failed and the static set ran instead. (v1.10.84 fix)
2. **Recommendation rows fully visible**, with translucent dark pill behind text on top of the in-game scene. No bottom clipping. (v1.10.84 fix — `--demo-recommendations` is the easiest way to populate 3 rows.)
3. **Ally damage-type bar shows real percentages**, not 0% / 0%. Should match whatever your team comp is. (v1.10.85 fix)
4. **LiveCompanion right column** "Champion Power Spikes" line shows the L6 / L11 / L16 deterministic line. "Game Plan" body shows either real prose (LLM key configured) or a "Configure in Settings → API Keys" hint (no key). NOT an indefinite "Generating…" message. (v1.10.86 fix)
5. **LLM key change in Settings takes effect immediately** — no app restart needed. (v1.10.87 + v1.10.88 fix)
6. **Power Spikes bar reflects team comp** — early/mid/late split should track the actual ally + enemy champion mix, not stay 0/5/0. (v1.10.90 fix)
7. **Counter-pick suggestions show "vs AP-heavy → Mercury's Treads" type reasons** when the build adapter modifies the base build. (v1.10.91 fix)
8. **Toggling Settings → Widgets while in-session works without restart**:
   * Telemetry / Diagnostics: log line shows start/stop on each Save (v1.10.93)
   * Focus Mode: rec panel collapses / expands immediately (v1.10.94)
   * Show Summoners / Show Spikes: title-bar panels appear/disappear (v1.10.96)
   * Show Scoreboard / Show Minimap Timers: same, both directions (v1.10.99)
9. **Saving Settings repeatedly doesn't leak** — open Activity Monitor / Task Manager, check the process's open-handle count after 5+ Saves. Should stay flat. (v1.10.97)

## Wire-bug pattern — what to watch for

Every bug this cycle was the same shape: **a Settings change or
upstream data flow exists, but a runtime consumer isn't actually
wired to react.**

Common smells:

* `lambda _key: []` or `dict()` stub at a call site (v1.10.85, v1.10.90).
* `if persisted.X:` gating widget/service construction (v1.10.99 — needs
  construct-then-hide).
* `_build_X(...)` in `_on_settings_changed` that recreates a service
  instead of mutating the running one (v1.10.97 — leak).
* Field on `SessionView` populated by view_builder but no UI consumer
  (`gaps`, `suggestion_builds` until v1.10.91).
* Service holding state from `__init__` that the user can change in
  Settings later (v1.10.88 LLM key, v1.10.97 region).

The cleanest fix is usually `set_X` / `apply_X` mutators on the
running object rather than rebuild — preserves post-init state
(connection pools, in-flight tasks, attached fetchers, patch).

## Live Companion — current state

File: [src/champ_assistant/ui/live_companion_view.py](../src/champ_assistant/ui/live_companion_view.py).
Wired into `MainOverlay` in `update_view` and the
`_apply_champ_select_subphase` machine.

### What's live

* **Top header**: "Live Companion" title + LIVE pill badge.
* **Team summary row**: 5 ally portraits | ally power-spike bar | ally
  damage-type bar | "vs" | enemy damage-type | enemy power-spikes | 5
  enemy portraits.
  - Damage type and phase distribution both pre-computed in
    `view_builder` and surfaced via `SessionView.{ally,enemy}_damage_profile`
    + `SessionView.{ally,enemy}_phase_distribution`.
* **Body, 3 columns**:
  - **Left** (`_BuildCard` + `BansColumn` + `PicksColumn`): champion
    icon + name + role, "Recommended Builds" line surfacing real
    `view.my_champion_build`, "Matchup Specific" with up to 3 enemies
    + counter score%, ban suggestions, pick suggestions with build
    adaptation reasons line.
  - **Center** (`_ItemsPanel`): runes row with icons, summoner spells
    line, item path with arrow separators.
  - **Right** (`_GamePlanPanel`): Early/Mid/Late phase pills
    (decorative tabs — not yet clickable), prose body driven by
    `view.game_plan_text` (LLM cache) with 4-state empty rendering,
    Champion Power Spikes section, Playing Against threat-summary.

### Deferred items (open feature work)

1. **Vision services construct-then-hide** — toggle-on from boot-off
   still needs a restart for `enable_auto_camp_detection` /
   `enable_scoreboard_detection`. v1.10.98 covered the toggle-off side
   (stop running services). Subtler than widgets because each wraps a
   worker thread + native capture handle that may be expensive to
   construct on a low-resource machine.
2. **Ally-roster panel in LiveCompanion** — the `b53fa9e` user feature
   ask ("loading-screen mains/winrate/last-10 for all 10 players").
   Fetch paused in v1.10.92, storage + view-builder wiring intact for
   one-line restore once the panel ships.
3. **`my_champion_tags` on SessionView** — would let `_spike_summary`
   show real per-champion phase signal instead of the generic "L6 / L11
   / L16 ult" line.
4. **`SessionView.gaps` cleanup** — field is computed but no UI reads
   it (only used internally as input to the picks algorithm).

## Important constraints to remember

These are in user auto-memory but worth surfacing here too:

* **Reliability → Performance → Intelligence → Features.** Never
  reverse. Features are last priority.
* **Design system lockdown** — UI styling must come from
  `ui/styles.py` tokens only. Linter at
  `tests/lint/test_design_lockdown.py` enforces no inline px / no raw
  hex.
* **Click-to-lock policy** — pick / ban suggestion clicks commit
  (completed:true), not hover.
* **Autonomous progress** — user's preferred mode is "keep moving";
  don't stop and ask between phases unless there's a real design
  decision. They'll say "stop" or "continue".
* **Commit cadence** — sensible commits as you go, logical units. Tag
  + push every meaningful version.
* **Type-ignore ratchet baseline at 94** — this cycle didn't grow it.
  Don't add new ignores without justifying AND lowering elsewhere —
  `tests/lint/test_typing_escape_hatches.py` enforces.

## Repro / dev commands

```bash
# Test suite (default — skips soak):
.venv/bin/pytest tests/ -q --ignore=tests/soak

# Cold-start regression only:
.venv/bin/pytest tests/perf/test_cold_start.py -v

# Bench harness (engine measurement):
.venv/bin/python scripts/bench.py --iterations 500 --summary-only --per-rule

# Launch the app in dry-run mode (uses fixture, no LCU needed):
.venv/bin/python -m champ_assistant --dry-run \
  --fixture tests/fixtures/sessions/04_my_turn_top.json --log-level INFO

# Stop a running dev launch (the qasync child is sneaky on macOS;
# see project_app_shutdown_quirk memory):
pkill -9 -f champ_assistant
```

On Windows the lockfile reads + Python 3.11 build will be the live
test surface. Watch for:

* DataDragon item icon `7028` returns 403 — single asset deprecated
  by Riot; non-fatal. No fix needed.
* SmartScreen warning on first run of the released exe — documented
  in README + release notes; user clicks More info → Run anyway.
* Vanguard-flagging if the dev tries to inject hotkeys etc. Per
  ARCHITECTURE there's a `tests/lint/test_no_input_hooks.py` linter
  guarding against that.

## End

Anything not in this file → check git log:
`git log --oneline 8c1501a..HEAD` covers the whole session arc since
the last commit before v1.10.55.

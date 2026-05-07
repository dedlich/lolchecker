# Session continuation — v1.10.88

Hand-off note for the next session. Read this first if you've been
dropped into this repo cold. Earlier handoff at v1.10.80 still describes
how the engine / __main__ / settings refactors landed; this section
covers the v1.10.81 → v1.10.88 polish round.

---

## Where we are (latest)

* **Tag**: `v1.10.88` (cut on macOS dev box, pushed to `origin/main`).
* **Suite**: 1655 / 1655 green via
  `.venv/bin/pytest tests/ -q --ignore=tests/soak`.
* **User platform switch**: macOS sessions wrote the code; user is
  switching to Windows to run against a live League client. **The
  v1.10.83 → v1.10.88 fixes are based on a Windows ranked-session
  bug report — they need a real-game smoke test before we know they
  hold up.**

## v1.10.81 → v1.10.88: live-test polish round

(Filling in since the prior handoff at v1.10.80.)

| Tag | Subject |
|---|---|
| 1.10.81 | Absorbed legacy `_my_build_panel` + `_picks_row` into LiveCompanion (CONTINUATION options #1 + #2) |
| 1.10.82 | Absorbed BanPanel into LiveCompanion + LLM game-plan prose (options #3 + #4) |
| 1.10.83 | Live-test fixes from a Windows session — Meraki pushed at champ-select lock-in, scoreboard hotkey-only, recommendation rows in pill |
| 1.10.84 | Build push timing race fixed (Meraki now in same LCU connection); rec-panel pill restored; ally/pick wire tests added |
| 1.10.85 | Ally damage-type bar 0% / 0% bug — `_tags_for` was a stub returning `[]`; now plumbed via `view_builder._compute_ally_damage_profile` + `SessionView.ally_damage_profile` |
| 1.10.86 | LiveCompanion empty-state stubs cleaned: game-plan body has 4 states (cached / in-flight / disabled-needs-config / pre-lock); Champion Power Spikes shows real text; Recommended Builds line surfaces real `view.my_champion_build` info |
| 1.10.87 | `_on_settings_changed` rebuilt profile service but NOT game-plan service — adding an LLM key in Settings left it disabled until restart. Rebuilt service on settings change. |
| 1.10.88 | Same bug for `RuntimeCounterStore` + cross-patch cache pollution because `_game_plan_llm.set_patch` was never called. Cleaner fix: in-place `set_credentials(api_key, provider)` on both services (preserves post-init state); wired the missing `set_patch` call. |

## Live testing checklist (pull on Windows + open a champ-select)

These are the things to verify the v1.10.83+ fixes actually held up:

1. **Build pushed at lock-in shows a 3-block Meraki blueprint** (Starting / Build Order / Situational, ~13 items total) NOT a 4-item static set. Status bar should read `Apply Build {Champion}: Items aktiviert` (Meraki path) — if it says `Items (Fallback)` then Meraki failed and the static set ran instead. (v1.10.84 fix)
2. **Recommendation rows fully visible**, with translucent dark pill behind text on top of the in-game scene. No bottom clipping. (v1.10.84 fix — `--demo-recommendations` is the easiest way to populate 3 rows.)
3. **Ally damage-type bar shows real percentages**, not 0% / 0%. Should match whatever your team comp is. (v1.10.85 fix)
4. **LiveCompanion right column** "Champion Power Spikes" line shows the L6 / L11 / L16 deterministic line (not the old "coming with LLM iteration" text). "Game Plan" body shows either real prose (LLM key configured) or a "Configure in Settings → API Keys" hint (no key). NOT an indefinite "Generating…" message. (v1.10.86 fix)
5. **LLM key change in Settings takes effect immediately** — no app restart needed. Configure an OpenRouter / Groq / Gemini key, save Settings, lock in a champion, see game-plan prose appear on the next snapshot. (v1.10.87 + v1.10.88 fix)
6. **Ban / pick row click commits the action via LCU** — clicking a ban row at ban time should send the LCU `commit_action`. Clicking a pick row at pick time the same. Status bar surfaces the result. (Wires verified by `tests/ui/test_live_companion_wires.py` v1.10.84.)

## Common-cause-to-watch-for: "wire silently dropped on a refactor"

The v1.10.85 / .87 / .88 bugs were all the same shape: **a service or
view component holds state set once at startup, but the user-visible
update path doesn't propagate later changes.** Examples found:

* `_tags_for` was a stub returning `[]` → ally damage profile always
  empty (v1.10.85)
* `_on_settings_changed` rebuilt only one of three services that
  read LLM credentials (v1.10.87 / v1.10.88)
* `set_patch` was wired for runtime_counters but never for the new
  game-plan service (v1.10.88)

If you find another instance, the cleanest fix is an in-place
`set_credentials` / `set_patch` mutator on the service rather than a
rebuild — preserves post-init state (lolalytics fetcher, patch,
in-flight task map).

## What this session shipped (v1.10.55 → v1.10.80, 26 versions)

Big arcs, in order:

1. **Phase 3 of [docs/OPTIMIZATION.md](OPTIMIZATION.md)** — declarative
   suppression table + split `_rules.py` 3,930 → 386 LOC across 8 domain
   files in [advisor/decision_engine/rules/](../src/champ_assistant/advisor/decision_engine/rules/).
   Caught a latent bug in `rule_far_behind_safe` along the way.

2. **Phase 4 §3.3** — split `__main__.py` 1,942 → 116 LOC into
   `cli.py` + `runtime_factory.py` + `boot.py` + `bootstrap_installer.py`.

3. **Phase 4 §3.5** — split `settings_dialog.py` 665 → 245 LOC into
   `ui/settings_sections/` (one file per tab: widgets, api, hotkeys,
   vision, diagnostics).

4. **Phase 5** — `scripts/bench.py` (engine measurement;
   75 µs/eval baseline, no rule is hot enough to motivate the §2.1
   grouping refactor), `.github/workflows/nightly.yml` (soak),
   `pyproject.toml` coverage gate at 70 % (currently 86.5 %).

5. **§2.3** — cold-start regression test in [tests/perf/test_cold_start.py](../tests/perf/test_cold_start.py)
   with a 1500 ms ceiling (charter A target is 1000 ms;
   actual baseline 230-380 ms). Import policy documented in
   [ARCHITECTURE.md](ARCHITECTURE.md).

6. **§4.4** — committed `requirements.lock`; `build.yml` installs
   from it instead of resolving fresh. **Caveat**: lock was
   resolved on Python 3.13 locally; the build job uses 3.11. If the
   next tag build hits a resolution mismatch, regenerate with
   `python3.11 -m pip-compile …` and re-commit. Pip-compile command
   is in the lock file's header.

7. **§4.1** — code signing deferred (no cert). Documented the
   SmartScreen warning in README + the GitHub Release body
   (auto-prepended above the changelog by `build.yml`).

8. **§4.5** — repo-root `data/` → `static/` (eliminated the collision
   with the Python package `champ_assistant.data`). Bundle path
   `_internal/data/` → `_internal/static/` in lockstep.

9. **Cleared 5 stale red tests** that had been sitting on `main`
   (recommendation_panel chat-redesign drift, retired
   click-to-arm map overlay, scoreboard visual snapshot baseline).

10. **Live Companion view (3 commits, v1.10.78 → v1.10.80)** —
    new unified champ-select layout matching the screenshot the user
    pasted. See next section.

## Live Companion — current state

File: [src/champ_assistant/ui/live_companion_view.py](../src/champ_assistant/ui/live_companion_view.py).
Wired into `MainOverlay` in `update_view` and the
`_apply_champ_select_subphase` machine.

### What's live

* **Top header**: "Live Companion" title + LIVE pill badge.
* **Team summary row**: 5 ally portraits | ally power-spike bar | ally
  damage-type bar | "vs" | enemy damage-type | enemy power-spikes | 5
  enemy portraits.
  - Power-spike phase derived from `static/tags.json` heuristic
    (Early-Game / Late-Game / Hyper-Carry / Scaling tags).
  - Damage type is `enemy_damage_profile` for enemy side; ally side
    falls back to a tag-heuristic until `view_builder.py` plumbs
    ally damage profiles through `SessionView`.
* **Body, 3 columns**:
  - **Left** (`_BuildCard`): champion icon + name + role, "Recommended
    Builds" line, "Matchup Specific" with up to 3 enemies + counter
    score%.
  - **Center** (`_ItemsPanel`): runes row with icons (uses
    `overlay._rune_icons`), summoner spells line, item path with arrow
    separators (uses `overlay._item_icons`). Empty state until
    `view.my_champion_build` is populated.
  - **Right** (`_GamePlanPanel`): Early/Mid/Late phase pills
    (decorative tabs — not yet clickable), prose body, Champion
    Power Spikes section, Playing Against threat-summary.

### What's still placeholder

1. **Game-plan prose** (right column) — the layout's there, no text
   generator behind it. This is **option 2** from the original
   scoping: feed the locked roster to the LLM provider already wired
   (`secrets.llm_api_key()` / `secrets.llm_provider()` — supports
   OpenRouter / Groq / Gemini, the same provider used by
   `RuntimeCounterStore`). Recurring API cost.

2. **Real ally damage profiles** — currently tag-heuristic. Fix is in
   `view_builder.py` — compute `enemy_damage_profile` for ally side too
   and add a parallel `ally_damage_profile` field on `SessionView`.
   Small change, no design risk.

3. **"Recommended Builds" left-column line** — placeholder text. Could
   show `view.my_champion_build.name` as the build label + the variant
   list as a click-cycle.

4. **Real win rates** — currently uses the counter score (0-10 scale)
   surfaced as a percentage. Honestly labelled but not a real WR.
   Option 3 from the original scoping (lolalytics scrape) — heavy
   lift, low priority.

## What I told the user is "next" (most recent message)

Three options I offered, ranked by scope:

1. **(smallest) Remove the legacy `_my_build_panel`** in
   [overlay.py](../src/champ_assistant/ui/overlay.py) — fully duplicated by
   LiveCompanion's center column. ~5 lines.
2. **(medium) Fold `_picks_row` into LiveCompanion's left column** —
   absorbs counter / synergy pick suggestions inline with "who you're
   up against". ~30 LOC.
3. **(big) Wire LLM game-plan prose** — option 2 from original
   scoping; recurring API cost.
4. **(biggest) Absorb the ban panel into LiveCompanion** so the legacy
   stack disappears entirely.

User went to Windows before picking. Next session: ask which one (or
all of them in order) before touching code.

## Important constraints to remember

These are in user auto-memory but worth surfacing here too:

* **Reliability → Performance → Intelligence → Features.** Never
  reverse. Features are last priority. Live Companion is a feature
  branch — UI work that landed because the user asked, not because
  the strategy charter prioritised it.
* **Design system lockdown** — UI styling must come from
  `ui/styles.py` tokens only. Linter at
  `tests/lint/test_design_lockdown.py` enforces no inline px / no raw
  hex. LiveCompanion was written respecting this; if you add UI code,
  pull every value from `styles.*`.
* **Click-to-lock policy** — pick / ban suggestion clicks commit
  (completed:true), not hover. If you wire pick suggestions into
  LiveCompanion's left column, follow this.
* **Autonomous progress** — user's preferred mode is "keep moving";
  don't stop and ask between phases unless there's a real design
  decision. They'll say "stop" or "continue".
* **Commit cadence** — sensible commits as you go, logical units. Tag
  + push every meaningful version.
* **Type-ignore ratchet baseline at 95** (down from 99 after
  LobbyStatsWidget removal). Don't add new ignores without justifying
  AND lowering elsewhere — `tests/lint/test_typing_escape_hatches.py`
  enforces.

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
test surface for v1.10.80. Watch for:

* DataDragon item icon `7028` returns 403 — single asset deprecated
  by Riot; non-fatal. No fix needed.
* SmartScreen warning on first run of the released exe — documented
  in README + release notes; user clicks More info → Run anyway.
* Vanguard-flagging if the dev tries to inject hotkeys etc. Per
  ARCHITECTURE there's a `tests/lint/test_no_input_hooks.py` linter
  guarding against that.

## Files most likely to need editing next

If the user picks #1 (remove `_my_build_panel`):
- [src/champ_assistant/ui/overlay.py](../src/champ_assistant/ui/overlay.py) — search `_my_build_panel`,
  `_my_build_champ_icon`, `_my_build_champ_label`, `_update_my_build`.
  Roughly lines 216-250 (build) and the `_update_my_build` method.

If the user picks #2 (fold picks row):
- Same overlay.py — `_picks_row`, `_counter_col`, `_synergy_col`,
  `_update_picks`, `_make_pick_row`. Move the column-building code
  into a new `_PicksColumn` widget under
  `ui/live_companion_sections/`, mirror the `settings_sections/`
  pattern. Drop `_picks_row` from overlay.

If the user picks #3 (LLM game plan):
- New file `src/champ_assistant/advisor/game_plan_llm.py` — analogous
  to `data/runtime_counters.py` (which already uses the LLM provider).
- Trigger: when `view.my_champion_key` changes from "" to a value,
  fire a one-shot async call. Cache by (champ, role, enemy_team_hash)
  to avoid re-paying on every tick.
- `_GamePlanPanel.update_panel` reads the cached prose if available,
  shows the placeholder otherwise.

## End

Anything not in this file → check git log:
`git log --oneline 8c1501a..HEAD` covers the whole session arc since
the last commit before v1.10.55.

# Architecture

This document captures the system design of Champ Assistant — subsystem
boundaries, data flow, the design-token contract, and the test strategy.
For user-facing intro see `../README.md`.

## Layered model

```
┌─────────────────────────────────────────────────────────────────┐
│  UI (Qt6 / qasync)                                              │
│   MainOverlay · PickCard · BanPanel · EnemyRow · LobbyStats    │
│   RecommendationPanel · MinimapTimers · ScoreboardOverlay      │
│   styles.py (closed visual contract)                            │
├─────────────────────────────────────────────────────────────────┤
│  Application orchestration                                       │
│   ChampAssistant · SessionView · StateStore · RenderScheduler   │
│   LifecycleManager                                               │
├─────────────────────────────────────────────────────────────────┤
│  Advisors (pure logic)                                          │
│   counters · picks · ban_suggestions · build_adapter            │
│   decision_engine                                                │
├─────────────────────────────────────────────────────────────────┤
│  Data sources                                                    │
│   LcuSource (WS) · LcdaSource (HTTP poll) · RiotApiClient       │
│   DataDragon (champ + item + rune icons)                        │
├─────────────────────────────────────────────────────────────────┤
│  Reliability + Performance infra                                │
│   crash_report · safe_mode · state_validator · health_monitor   │
│   performance_monitor · diagnostics · telemetry                  │
└─────────────────────────────────────────────────────────────────┘
```

Every UI widget consumes a `SessionView` (immutable Pydantic model) — never the raw
LCU / LCDA payloads. `ChampAssistant` is the orchestrator that takes session updates
from the LCU and emits views; `StateStore` is the in-game state's pub/sub source of
truth.

## Data flow

### Champ-select pipeline

```
LcuSource (websocket subscription on champ-select-v1-session topic)
    │  emits {"type": "session", "data": ...}
    ▼
ChampAssistant.consume(events)
    │  validates payload → ChampSelectSession (pydantic)
    │  derives: enemy_counters, enemy_keys/names, enemy_roles,
    │           enemy_damage_profile, picks, gaps, ban_suggestions,
    │           suggestion_builds (matchup-adapted), enemy_profiles,
    │           ally_profiles
    │  fans out async profile fetches → RiotApiClient
    ▼
SessionView (frozen) → MainOverlay.update_view(view)
    │  routes to: enemy rows, pick suggestions, ban panel, lobby stats,
    │  recommendation panel
```

### In-game pipeline

```
LcdaSource (1 Hz HTTP poll of localhost:2999)
    │  emits LcdaSnapshot (game_time + objectives + per-player
    │  KDA / level / items_value)
    ▼
on_snapshot()
    │  → StateStore.update(lcda_snapshot=..., game_time=..., ...)
    │  → health_monitor.report_recovery("lcda_pipeline")
    │  → state_validator (subscriber on store)
    │  → decision_engine.evaluate(snapshot) → recommendations
    ▼
StateStore listeners
    │  → MainOverlay.update_lcda_snapshot
    │  → MinimapTimersWidget.update_snapshot
    │  → RecommendationPanel.set_recommendations
    │  → ScoreboardOverlayController (gold diff)
    │  → SummonerTracker (cooldown render)
```

The `StateStore` deduplicates updates — listeners only fire on actual changes, so a
no-op poll doesn't trigger repaints.

## Subsystem boundaries

### `advisor/`

**Pure functions** that take typed session inputs and return decisions. No Qt, no
network, no I/O. Testable in isolation. Examples:

* `counters.lookup_counters(champion_key, role) -> list[CounterEntry]`
* `picks.suggest_picks(session, counters, tags, ...) -> (suggestions, gaps)`
* `ban_suggestions.suggest_bans(session, ..., my_role=..., limit=3)`
* `build_adapter.adapt_build(base, role, enemy_team_keys, tags)`
* `decision_engine.evaluate(snapshot) -> list[Recommendation]`

The decision engine has **57 callable rules** (53 in `ALL_RULES` + 3 spell-tracker
rules + `rule_situational_build`). Each is a pure function; buggy rules are isolated
by per-rule try/except in `evaluate`. The full catalog grouped by category lives in
[../RULES.md](../RULES.md), which is kept in sync with the code by
`tests/lint/test_rules_catalog.py` (CI fails if a callable rule lacks a
`### `rule_xxx`` heading or vice-versa). Per-rule timing + activation telemetry
flushes to `rule_timing.log` on shutdown for live diagnostics.

### `lcu/`

League Client integration — lockfile-based localhost HTTPS + websocket subscription.

* `lockfile.find_lockfile()` — locates Riot's auth file
* `client.LcuClient` — async httpx wrapper with retry + 5xx backoff
* `events.parse(frame)` — strict opcode + topic-string filter on WS frames
* `sources.LcuSource` — emits champ-select session updates
* `champ_select.{hover_action, commit_action}` — PATCH /lol-champ-select/v1/session/actions
* `perks.apply_rune_page` — POST /lol-perks/v1/pages
* `item_sets.apply_item_set` — PUT /lol-item-sets/v1/item-sets

Every LCU mutator is hover-or-commit only. **No keyboard / mouse hooks** anywhere in
the codebase — Vanguard-incompatible.

### `lcda/`

In-game Live Client Data API (`https://127.0.0.1:2999/liveclientdata/...`).

* `client.LcdaClient` — async polling with backoff
* `source.LcdaSource` — owns the poll loop, builds `LcdaSnapshot` per tick
* `objectives.compute_objectives` — derives Dragon / Baron / Herald respawn timers
  from kill events
* `players.LivePlayer` — KDA + level + items_value + spells per active-game player
* `power_spikes.detect` — diff vs prior snapshot for level / item milestones
* `spell_tracker` — manual click-to-time enemy summoner cooldowns

### `data/`

* `models.py` — Pydantic v2 frozen dataclasses for every domain object
  (Champion, ChampSelectSession, ChampionBuild + variants, etc.)
* `loader.py` — atomic JSON loaders for `data/{builds,counters,tiers,tags}.json`
* `datadragon.py` — DataDragon CDN client with disk-cached PNG bytes
* `runtime_counters.py` — patch-update workflow for counters.json
* `items_data.py` + `perks_data.py` — name → ID maps for LCU rune-page / item-set
  POST payloads

### `profiling/`

Riot Web API client + EnemyProfile composer.

* `riot_api.py` — strict puuid-only endpoints (`summoner/v4/by-puuid`,
  `league/v4/entries/by-puuid`, `match/v5/...`, `champion-mastery/v4/by-puuid`).
  Riot's by-name and by-summoner-id forms were retired during the Riot ID
  migration; we don't call them.
* `profile.py` — `ProfileService.fetch_by_puuid(puuid)` fans out
  mastery + match-summaries + rank in parallel; derives `(wins, losses, streak)`
  AND `role_winrates` from the same set of match-v5 fetches (single-fetch path).

### `ui/`

* `styles.py` — closed visual contract. Every color / spacing / radius / typography
  size token is `Final` here; inline drift is caught by
  `tests/lint/test_design_lockdown.py`.
* `floating_widget.py` — base for all top-level overlay widgets (frameless,
  always-on-top, transparent-friendly, drag-to-move with persistence).
* Per-widget files mirror their domain (`pick_card.py`, `ban_panel.py`, etc.).

### Reliability + Performance infra

* `lifecycle.py` — ordered startup, reverse shutdown. Every subsystem registers a
  stop callback; shutdown walks the list in reverse so producers stop before
  consumers.
* `crash_report.py` — global excepthook → JSON dump. Increments a crash counter that
  trips Safe Mode after N consecutive crashes.
* `safe_mode.py` — disk marker file gates risky subsystems on next boot.
* `state_validator.py` — pure invariant checks + `StateValidator` observer that
  logs violations.
* `health_monitor.py` — per-service failure counter + restart callback registry +
  exponential backoff.
* `performance_monitor.py` — `record_phase(name)` ring-buffer; flushes to
  `performance.log` on shutdown. Wired at run_with_ui_entry, core_services_initialized,
  ui_visible.
* `diagnostics.py` — periodic CPU / RAM / FPS log. State-store-attached.
* `telemetry.py` — append-only JSONL event log. Local-only, no network.

## Design system

`styles.py` is the single source of visual truth. Tokens are categorized:

* **Backgrounds** — `BG_PRIMARY` ... `BG_INTERACT` ... `BG_HIGHLIGHT`
* **Text** — `TEXT_PRIMARY` ... `TEXT_DISABLED`
* **Brand + state** — `ACCENT`, `DANGER`, `WARNING`, `SUCCESS`, team colors
* **Borders** — translucent rgba so they soften under panels
* **Tier colors** — `TIER_S_PLUS` ... `TIER_D` for ranking badges
* **Typography** — `FS_CAPTION` (10px) ... `FS_DISPLAY` (22px)
* **Spacing** — 4-pt grid: `SPACING_TIGHT` (4) `_GRID` (8) `_WIDE` (12) `_LOOSE` (16)
* **Radius** — `RADIUS_SMALL` (4) `RADIUS` (8) `RADIUS_LARGE` (12) `RADIUS_PILL` (999)
* **Shadows** — `SHADOW_FLOAT` / `SHADOW_PANEL` / `SHADOW_HOVER` profiles

CI lints (`tests/lint/test_design_lockdown.py`) reject:
* Inline `Npx` literals outside the typography scale
* Bare `#RRGGBB` hex codes outside the styles.py allowlist

## Build + deploy

* `pyproject.toml` — single source of metadata. `version` matches
  `src/champ_assistant/__init__.__version__`.
* `.github/workflows/test.yml` — pytest on macOS + Windows × Python 3.11 + 3.12
  on every push / PR.
* `.github/workflows/build.yml` — PyInstaller one-folder Windows bundle on tag
  push. Uploads `champ-assistant-windows.zip` to GitHub Releases.
* PyInstaller spec at `scripts/build_windows.spec`. One-folder (not one-file)
  deliberately — Antivirus flags one-file PyInstaller binaries far more often.

## Test strategy

```
tests/unit/       # pure logic — advisor / data / profiling / engine
tests/ui/         # Qt widget smoke + structural snapshots (offscreen Qt)
tests/integration/ # full session-payload → SessionView pipelines
tests/game/       # gold_diff_service end-to-end
tests/lint/       # design lockdown (no inline px/hex), input-hook safety
```

* **No mocks for Pydantic models.** Validators run on every test fixture.
* **Async tests** use `pytest-asyncio` strict mode; httpx mocks via `respx`.
* **Qt tests** use `QT_QPA_PLATFORM=offscreen` for headless CI.
* **Visual regression** — structural snapshots (widget tree shape), not pixel
  baselines — pixel snapshots are non-deterministic across platforms.
* **Coverage gate** at the orphan-detection level: every state-vector that the
  store produces must have at least one widget rendering it.

## Conventions

* **Pydantic v2 frozen dataclasses** for every cross-boundary payload — mutating
  state across async tasks is a bug, not a feature.
* **Defensive degradation** — every external call has a known-safe fallback. No
  crash should be reachable from a network blip, a malformed payload, or a missing
  config field.
* **Charter priority order is non-negotiable** —
  Reliability → Performance → Intelligence → Features. New work maps to a charter
  step before it ships.
* **Pure functions wherever possible** — advisor / decision-engine / state-validator
  / profile aggregators are all pure; UI widgets, async sources, and the orchestrator
  are the only stateful layers.

For long-form rationale see `masterplan-lol-champ-assistant-v3.md` at the repo root.

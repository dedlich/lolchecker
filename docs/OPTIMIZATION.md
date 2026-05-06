# Optimization & Refactoring Plan

**Status:** Draft — written 2026-05-06 against `main @ 2e83e78` (v1.10.51).
**Scope:** Concrete, verified findings — every item below is grounded in a current
file or line. Ordered by the project charter:
**Reliability → Performance → Intelligence → Features.**

---

## 0. Headline numbers

| Metric                                  | Value     | Comment                                                            |
| --------------------------------------- | --------- | ------------------------------------------------------------------ |
| Source LOC (`src/champ_assistant`)      | ~37,925   | `wc -l` on all `.py` files                                         |
| Largest single file                     | 3,929 LOC | [advisor/decision_engine/_rules.py](../src/champ_assistant/advisor/decision_engine/_rules.py) |
| Largest UI file                         | 916 LOC   | [ui/overlay.py](../src/champ_assistant/ui/overlay.py)              |
| Largest entry point                     | 1,918 LOC | [__main__.py](../src/champ_assistant/__main__.py)                  |
| `# type: ignore` markers                | 96        | strict mypy is being widely bypassed                               |
| `noqa` markers                          | 72        | ruff being widely bypassed                                         |
| Decision-engine rule functions          | 61        | docs say 11 — see [§3.1](#31-documentation-drift-rules-engine)     |

Top 5 files together ≈ 9.5 kLOC — about a quarter of the source. Splitting
those is the single biggest unlock for parallel work and reading speed.

---

## 1. Reliability (charter pillar C)

### 1.1 Version is duplicated — single source of truth

The version lives in **both** [pyproject.toml:7](../pyproject.toml#L7) and
[__init__.py:3](../src/champ_assistant/__init__.py#L3). Recent commit history
(`2e83e78`, `f403fe4`, `5a0719a`) shows the two are bumped in lockstep — easy
to miss one and ship a build whose `--version` and PyPI metadata disagree.

**Fix:** Drop the literal in `__init__.py`, derive at runtime:

```python
from importlib.metadata import version, PackageNotFoundError
try:
    __version__ = version("champ-assistant")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
```

PyInstaller bundles ship dist-info, so `version()` works in the frozen exe too.
Saves one manual step on every release and removes a class of release-day
mismatches.

### 1.2 `_dump_failed_payload` walks logging handlers to find a path

[app.py:213-227](../src/champ_assistant/app.py#L213-L227) iterates the root
logger's handlers and reads `baseFilename` to discover where to write a failure
dump. This is brittle — if the rotating file handler is reorganized or replaced
with a JSON handler, the dump silently goes nowhere (the function swallows the
exception).

**Fix:** Introduce a tiny `app_paths` module that owns the resolved log /
state / cache directories. `logging_setup` and `_dump_failed_payload` both
call into it. One source of truth, no handler introspection. Same pattern can
absorb the scattered `Path(__file__).resolve().parents[N]` computations
visible in `__main__.py:_resource_root`.

### 1.3 Strict-mypy escape hatches are normalized

96 `# type: ignore` and 72 `noqa` markers across `src/`. Most clusters live
around two patterns:

- **`**kwargs` orchestration** — see
  [app.py:281-293](../src/champ_assistant/app.py#L281-L293): the
  `_ban_kwargs` dict is sprayed through `suggest_bans(**_ban_kwargs, ...)`
  twice with `# type: ignore[arg-type]`. A small typed `BanQuery` dataclass
  removes both ignores without rewriting the call site.
- **Qt signal payloads** — `pyqtSignal(str, "PyQt_PyObject", ...)` forces
  ignores at every connection. PyQt6 supports typed signal stubs via
  `typing.cast`; or wrap signals in a typed helper.

**Action:** quarterly audit. Track the count in CI; new ignores need a
linked issue. Strict mypy is only useful if it actually bites.

### 1.4 Stale Phase-6 fossil: hardcoded `_STARTER_CHAMPIONS`

[__main__.py:77-108](../src/champ_assistant/__main__.py#L77-L108) carries a
30-champion hardcoded list with the comment *"Phase 7 replaces this with a
live DataDragon fetch (cached on disk)"*. The repo already has a populated
`ddragon_cache/` directory — Phase 7 happened, the fossil never left.

**Risk:** if a code path falls back to the starter dict (e.g. ddragon cache
miss + offline), the user gets a 30-champion universe — counters and tier
lookups silently degrade. State invariant: champion index size must equal
ddragon roster size; assert it once at boot.

**Fix:** delete the literal, route everything through
`data.datadragon.load_champion_index()`. If the cache is empty, fail loud at
boot instead of degrading silently.

### 1.5 Bootstrap installer flags hide in the public CLI

[__main__.py:140-143](../src/champ_assistant/__main__.py#L140-L143) tacks
`--bootstrap-staged`, `--bootstrap-install`, `--bootstrap-parent-pid` onto
the main argparser as `argparse.SUPPRESS`. They're a self-update implementation
detail spilling into the user-facing CLI — and the parser has no way to
mutually-exclude them from `--dry-run` etc.

**Fix:** make a real subcommand: `champ-assistant _bootstrap install --dir
... --parent-pid ...`. Hidden subcommand, never advertised in `--help`, but
its surface is clearly separated from user flags. Reduces the combinatorial
test space for entry-point parsing.

### 1.6 Manual `_inflight: set` deduplication

[app.py:122-123](../src/champ_assistant/app.py#L122-L123) tracks
`_runtime_inflight` and `_profile_inflight` as plain sets to avoid duplicate
async fetches. Hand-rolled — easy to forget the discard on the failure path,
and any new async fan-out needs another set.

**Fix:** a single `Coalescer` helper that wraps an async key→Task map and
guarantees discard via `try/finally`. Reuse for runtime counters, profile
fetches, and any future fan-out (e.g. summoner-spell metadata).

---

## 2. Performance (charter pillar A)

### 2.1 Decision engine evaluates all 61 rules every tick

[advisor/decision_engine/_rules.py](../src/champ_assistant/advisor/decision_engine/_rules.py)
has 61 `rule_*` functions; the evaluator runs each one per LCDA tick. Most
short-circuit early on `game_time`, but they all pay function-call + import
overhead.

**Fix paths in priority order:**

1. **Group rules by precondition** so the engine evaluates one group at a
   time. E.g. `objectives_group` only runs when `objectives` differ from
   prior tick; `summoner_cd_group` only when a tracked spell is on cooldown;
   `inhibitor_group` only when an inhib delta is present. Reuse the
   already-computed `StateStore` deltas
   ([state_store.py](../src/champ_assistant/state_store.py)) — that store
   already deduplicates, so feed it.
2. **Phase tags** — annotate each rule with the game-phase window it cares
   about (`early`, `mid`, `late`, `any`) and skip out-of-phase rules in
   bulk.
3. Once 1+2 land, **measure** before considering Cython / numba / numpy
   rewrites for the hot helpers in `_core.py`.

The point is correctness first: `decision_engine` is on the in-game render
path, but it's also the smartest part of the product (charter B). Don't
optimize it blindly — measure with `performance_monitor.record_phase` per
group.

### 2.2 LCDA poll interval drift between code and docs

`DEFAULT_POLL_INTERVAL = 2.0` in
[lcda/source.py:38](../src/champ_assistant/lcda/source.py#L38) — actually
0.5 Hz. The architecture doc says *"1 Hz HTTP poll"*
([docs/ARCHITECTURE.md:65](ARCHITECTURE.md#L65)). Either is defensible, but
both being claimed simultaneously isn't.

**Fix:** decide deliberately. 1 Hz costs 2× the CPU on the localhost loop
but gives recall/teamfight rules a tighter trigger. 2 s is fine for
objectives. Make the choice tunable per rule group (see §2.1) and align the
docs.

### 2.3 Lazy-import discipline is inconsistent

[__main__.py:257](../src/champ_assistant/__main__.py#L257),
[__main__.py:267-269](../src/champ_assistant/__main__.py#L267-L269),
[__main__.py:174](../src/champ_assistant/__main__.py#L174),
[__main__.py:201](../src/champ_assistant/__main__.py#L201) all do
`from champ_assistant import X as _x` *inside* functions for boot-speed.
Other heavyweight modules (`anthropic`, `httpx`, `keyring`,
`PyQt6.QtWidgets`) sit at the top of the file.

**Fix:** pick one rule. Recommended:

- **Top-level imports** for anything used at module-import time or for
  type-only contexts (`if TYPE_CHECKING`).
- **Lazy imports** for: subsystems gated by user settings (telemetry,
  diagnostics, vision), Anthropic / Riot API clients, and anything pulled
  in only on a code path that the typical session doesn't hit.

Then add a regression test:
`startup_summary.total_ms < 1000ms` on a cold cache (charter A target).
The summary log line is already wired
([__main__.py:43-56](../src/champ_assistant/__main__.py#L43-L56)) — assert
it.

### 2.4 `LcdaSnapshot` is a 17-field frozen dataclass allocated per tick

[lcda/source.py:46-83](../src/champ_assistant/lcda/source.py#L46-L83) — each
tick allocates a new `LcdaSnapshot` plus several nested lists, plus
ancillary objects (`ActiveCombatState`, `TiltState`, `GankAlert`, ...). At
0.5–1 Hz this is fine; if §2.1's per-group cadence pushes some rules to 5+
Hz, allocator pressure starts to matter.

**Fix:** keep the frozen `LcdaSnapshot` (immutability is load-bearing for
the rule engine) but split into a `Core` snapshot that always allocates and
an `Extras` namespace that only allocates on the tick where the relevant
sub-detector fires. Most ticks would skip allocating `gank_alert` /
`tilt_state` / `enemy_spikes` entirely.

### 2.5 Bench harness gap

`scripts/` has `telemetry_summary.py` and `preview.py` but no benchmark
script. `tests/soak` is opt-in but doesn't track regression numbers.

**Fix:** `scripts/bench.py` that loads N fixture snapshots and times
`decision_engine.evaluate()` per rule group. Output JSONL into
`performance.log` so the existing telemetry summary can pick it up. Run
nightly via a separate CI workflow; alert on >10% regression.

---

## 3. Code structure & maintainability

### 3.1 Documentation drift: "rules engine"

Three places state the engine has 11 rules:

- [README.md:48](../README.md#L48) — *"11 curated heuristics"*
- [docs/ARCHITECTURE.md:97-101](ARCHITECTURE.md#L97-L101) — names the 11
- [memory/project_strategy_charter.md] (auto-memory) — same claim

Reality: 61 `rule_*` functions in `_rules.py`. The 11 cited in
ARCHITECTURE.md are a strict subset (and `kill_lead_snowball` etc. exist,
but so do `rule_tilt_detection`, `rule_objective_setup_window`,
`rule_gank_risk`, `rule_situational_build`, ...).

**Fix:** auto-generate the rule list at build time from
`ALL_RULES` rather than maintaining a prose list. Drop a one-line
auto-extracted table into ARCHITECTURE.md via a tiny script in `scripts/`.

### 3.2 `_rules.py` is 3,929 lines / 152 KB / 61 functions

The module already shows refactor-fatigue tells:

- Wildcard import + explicit underscore re-import
  ([_rules.py:20-73](../src/champ_assistant/advisor/decision_engine/_rules.py#L20-L73))
  is what you write when you split fast and don't want to fix call sites.
- Hysteresis singletons live in `_state.py` but get re-bound by name in
  `_rules.py`.

**Fix — split by domain.** Suggested groups (each → its own file under
`advisor/decision_engine/rules/`):

| Group               | Members                                                                    |
| ------------------- | -------------------------------------------------------------------------- |
| `objectives.py`     | `drake_*`, `baron_*`, `herald_*`, `void_grubs`, `dragon_window`, `elder_*` |
| `inhibitors.py`     | `enemy_inhib*`, `ally_inhib*`, `enemy_base_exposed`                        |
| `bounty.py`         | `active_bounty`, `enemy_bounty`, `ally_bounty`, `objective_bounty_active`  |
| `summoner_cd.py`    | `enemy_flash_down`, `enemy_tp_down`, `enemy_combat_spell_down`             |
| `combat.py`         | `fight_*`, `numbers_*`, `ace`, `teamfight_outcome`                         |
| `lane.py`           | `lane_*`, `cs_deficit`, `matchup_mismatch`, `plate_window`                 |
| `personal.py`       | `recall_check`, `unspent_skill_points`, `tilt_detection`, `power_spike`    |
| `meta.py`           | `late_game_group`, `gold_lead_push`, `far_behind_safe`, `level_deficit`    |

Then `_rules.py` becomes a 50-line `__init__` that imports and aggregates
into `ALL_RULES`. Remove the wildcard import; expose `_core` symbols
through a proper public API.

This pays for itself the first time a rule needs review — `git blame` on a
domain-scoped file is readable.

### 3.3 `__main__.py` is 1,918 lines

Far past the size where an entry point belongs in one file. Mixes:

- argparse construction
- starter champion list
- Qt platform detection
- profile-service factory
- safe-mode gating
- subsystem startup with `_safe_start`
- bootstrap installer launch
- summary logging

**Fix:** keep `__main__.py` to the imperative narrative — `parse_args() →
build_runtime() → run() → exit()` — and lift the rest into siblings:

```
champ_assistant/
├── __main__.py              # ≤200 LOC
├── cli.py                   # argparse + arg validation
├── runtime_factory.py       # _build_assistant, _make_source, _build_profile_service
├── boot.py                  # _enable_gpu_backend, _safe_start, _run_with_ui flow
└── bootstrap_installer.py   # everything behind the hidden bootstrap subcommand
```

Each sibling stays under 400 LOC and is independently testable. `__main__`
becomes the single line `from champ_assistant.cli import main`.

### 3.4 `app.py` mixes orchestration, role inference, and view assembly

[app.py:48-81](../src/champ_assistant/app.py#L48-L81) defines
`infer_role_from_tags` between two import blocks
([app.py:36, 82](../src/champ_assistant/app.py#L82)) — a PEP-8 ordering
violation that's only a smell today but will break the next time someone
adds a top-level import that depends on the function.

`ChampAssistant._build_view` ([app.py:237+](../src/champ_assistant/app.py#L237))
runs nine derivations inline (counters, names, keys, roles, damage,
suggestions, builds, bans, my-build). Each is testable in isolation today
but lives in the orchestrator, so the orchestrator's tests have to set up
all nine at once.

**Fix:** extract `view_builder.py` with a single
`build_session_view(session, deps) -> SessionView` pure function. Move
`infer_role_from_tags` into `advisor/role_inference.py`. `ChampAssistant`
becomes a thin event/state machine that calls into the view builder.

### 3.5 UI: `overlay.py` (916), `settings_dialog.py` (665), `scoreboard_widget.py` (542)

These are god-widgets. Settings-dialog in particular grew with every new
toggle. Two surgical wins:

- **Settings sections** — split `settings_dialog.py` into `sections/`
  (hotkeys, api_keys, diagnostics, lowres, advanced) each implementing a
  `SettingsSection` protocol. Dialog assembles them in a tabbed layout.
- **Overlay composition** — `overlay.py` should not own pick-card layout
  AND ban-panel layout AND power-spike panel AND summoner-tracker AND
  status bar. Extract a `LayoutComposer` that returns the central widget
  and has its own snapshot tests in `tests/ui/visual`.

### 3.6 `_evaluate._suppress_dominated` is the right pattern, scaling badly

[_evaluate.py:26-100+](../src/champ_assistant/advisor/decision_engine/_evaluate.py#L26)
encodes suppression as imperative `if "x" in kinds:` blocks. With 61 rules,
every new rule needs to think about every existing suppression — the matrix
isn't auditable.

**Fix:** declarative suppression table —
`SUPPRESSED_BY: dict[str, set[str]]` keyed by *suppressor → set of
suppressed kinds*. The evaluator becomes a single graph walk. Tests can
assert table coverage (every rule kind appears as either a key or a value
or both, with documented intent).

---

## 4. DevOps & supply chain

### 4.1 Windows Defender false-positive papered over with a README warning

[README.md:80-82](../README.md#L80-L82) tells users to add the install
folder to Defender exclusions. That's a usability tax and a security smell
— users who do that for one app are more likely to do it for malware.

**Fix:** Authenticode-sign the bundled `champ-assistant.exe` and the
`python311.dll`. Costs ~$80–200/yr for a code-signing cert; removes the
warning entirely. EV cert is overkill for a personal-scale tool — OV is
enough for SmartScreen reputation to build.

If signing isn't on the table this quarter, at least:
- Sign with a self-signed cert and document fingerprint.
- Generate SHA256 sums alongside the release zip and surface them in the
  release notes.

### 4.2 CI matrix runs on push only; no nightly soak

[.github/workflows/test.yml](../.github/workflows/test.yml) (per
ARCHITECTURE.md:202) runs unit/integration on every push. `tests/soak/` is
explicitly ignored ([pyproject.toml:80](../pyproject.toml#L80)).

**Fix:** add `.github/workflows/nightly.yml` that runs `pytest tests/soak`
on a 6 h timeout. Cron at 03:00 UTC. Failures open a Linear/GitHub issue
via gh-cli. This is the only place memory leaks and reconnect storms get
caught before users see them.

### 4.3 No coverage gate in CI

`pyproject.toml` configures `coverage.run` but no threshold. README
mentions an "orphan-detection" gate; `tests/lint/` enforces visual lockdown.
No line-coverage floor.

**Fix:** start at `--cov-fail-under=70` for `src/champ_assistant/advisor`
and `src/champ_assistant/lcda` (they're pure). UI / lifecycle stays
untracked. Ratchet up by 5 points per quarter.

### 4.4 No reproducible-builds audit

`pyproject.toml` pins lower bounds (`>=`) but no upper bounds and no lock
file. PyInstaller bundles whatever resolved at build time. If a transitive
breaks (`pydantic 2.x → 3.x` will), the next tagged release builds against
a different graph than yesterday's release.

**Fix:** commit `requirements.lock` (pip-compile) at build time; verify
the build job uses `pip install -r requirements.lock --no-deps`. Reduces
"works on my CI" to "works on the locked set."

### 4.5 The `data/` directory is duplicated

`/data/builds.json` (repo root) and `/src/champ_assistant/data/` (Python
package) — different things, same name. New contributors confuse them
constantly.

**Fix:** rename the runtime-content directory to `/static/` or
`/content/`. Code-only renaming cost is negligible; mental-model cost over
a year is large.

---

## 5. Recommended sequencing

The charter forbids re-ordering Reliability before Performance before
Intelligence before Features. Within Reliability, do the cheap-and-load-bearing
items first:

| Phase | Tasks                                                                    | Effort   | Charter |
| ----- | ------------------------------------------------------------------------ | -------- | ------- |
| 1     | §1.1 single-source version, §3.1 auto-gen rule docs, §1.4 delete fossil  | 1 day    | C       |
| 2     | §1.2 app_paths, §1.6 Coalescer helper, §3.4 view_builder extraction      | 2-3 days | C       |
| 3     | §3.2 split `_rules.py` by domain, §3.6 declarative suppression table     | 3-5 days | C/B     |
| 4     | §3.3 split `__main__.py`, §3.5 settings_dialog sections                  | 2-3 days | C       |
| 5     | §2.5 bench harness, §4.2 nightly soak, §4.3 coverage gate                | 1-2 days | C/A     |
| 6     | §2.1 rule grouping + measurement, §2.4 split `LcdaSnapshot`              | 5+ days  | A       |
| 7     | §2.3 import discipline + cold-start regression test                      | 2 days   | A       |
| 8     | §1.3 type-ignore audit, §3.5 overlay LayoutComposer                      | ongoing  | C       |
| 9     | §4.1 code signing, §4.4 lockfile-driven builds                           | 1-2 days | C       |

Phases 1-2 unblock everything else without changing user-visible behaviour
— ship them first, then reassess.

---

## 6. What I deliberately did *not* recommend

- Rewriting the rule engine in another language. 61 Python rules at <2 Hz
  is not the bottleneck — measure first (§2.5).
- Replacing PyQt6 with anything else. Migration cost dwarfs every other
  item on this list combined; the existing visual-lockdown CI already
  contains UI churn risk.
- Adding a database. JSON + diskcache fits the workload; introducing
  SQLite would add a backup/migration story for zero perceived gain.
- Splitting into a multi-process architecture. Reliability infra
  (`crash_report`, `safe_mode`, `health_monitor`) already isolates
  subsystems within one process; multi-process buys little and costs IPC
  latency on the in-game render path.
- Touching the LCU integration surface. Any change to
  `lcu/champ_select.py` or `lcu/perks.py` risks Vanguard-flagging — leave
  it alone unless you're fixing a confirmed bug.

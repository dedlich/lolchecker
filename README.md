# Champ Assistant

> League of Legends Companion Overlay — counters, builds, decision hints, and player intel
> in a single transparent window that sits on top of LeagueClient.

Built to be **faster than Blitz**, **smarter than Porofessor**, and **more reliable than
either**. The product strategy is a strict three-pillar order:
**Reliability → Performance → Intelligence → Features.** Every change is mapped to that
order before it ships.

---

## What it does

### Pre-game (Champ Select)

* **Lane-targeted ban suggestions.** Top-3 bans scored against your assigned lane plus
  the enemy team's most-played champions (Riot match-v5 mains).
* **Pick suggestions** with counter-pick reasoning, score, and tier badge. Click a card →
  the champion auto-locks via LCU **and** the build (runes + items + summoner spells)
  pushes through in one shot.
* **Build-variant cycle** (◀ ▶) on cards with multiple curated builds.
  Currently shipping: Garen / Darius / Aatrox TOP, Yasuo MID, Lee Sin JG, Ezreal BOT,
  Lulu SUPPORT.
* **Item + rune icons** rendered inline (DataDragon CDN, prefetched + disk-cached).
* **Build adapter** — automatic boots / anti-heal / tenacity swaps based on enemy team
  composition (AP-heavy → Mercury's, sustain → Morellonomicon, heavy CC → Tenacity, etc.).
* **AP/AD damage badge** per enemy. Helps decide MR vs Armor priority at a glance.

### Loading screen

* **Both teams' player profiles** in a stacked panel: portrait, summoner name, rank,
  per-role winrate over the last 20 ranked-solo matches, top-3 mastery champs, current
  W-streak / L-streak, account level.
* **Behavior tags** as colored pills: `OTP MID`, `Autofill?`, `Hot W4`, `Tilt L5`,
  `Champ-Spec`, `Veteran`, `Newbie`. Derived from the same recent-matches sample, no
  extra fetches.

### In-game

* **Minimap timer overlay** — fully-transparent window pinned over the actual in-game
  minimap. Camp markers (R / B / G / K / P / W / S) at canonical SR positions; click
  one to mark it as observed-killed → countdown starts. Drake / Baron / Herald markers
  arm automatically from LCDA kill events.
* **Tab-scoreboard gold-diff panel** — only renders while the in-game TAB scoreboard is
  visible (vision-based detection). Shows team totals + per-lane matchup deltas with
  directional arrows, mirroring the in-game scoreboard layout.
* **Decision engine** with 57 curated rules (catalog: [RULES.md](RULES.md)) →
  recommendations as a notification-center-style panel (severity-colored strip +
  category glyph + body text). Examples:
  * `Drache spawnt in 25s — Vision setzen, Side gruppieren` (alert)
  * `+4500 Gold — Vision pushen, nächstes Objective vorbereiten` (info)
  * `-6200 Gold — Safe spielen, keine Fights` (warn)
* **Summoner spell tracker** — left-click an enemy spell icon when you see it cast; the
  cooldown counts down inline. Right-click to clear.
* **Power-spike highlights** — your level / item milestones flash as small toasts.

### Reliability + Performance

* **Crash-isolated subsystems.** Every service runs behind try/except boundaries; one
  failure can't kill the others. Crash reports auto-write JSON dumps for post-mortems.
* **Safe Mode** auto-trips on repeated crash and disables risky subsystems (vision,
  hotkeys, telemetry, update-check) until manually resumed.
* **State invariant validator** logs timer / snapshot / player invariant violations as
  warnings. Detection-only — never mutates state.
* **Health monitor** tracks per-service failure counts, fires registered restart
  callbacks on threshold breach, exponential-backoff between attempts.
* **Performance monitor** records named phase timestamps from process start; flushes
  to `performance.log` on shutdown for post-hoc startup auditing.
* **Low Resource Mode** — single switch in Settings → Diagnostics that disables
  vision / telemetry / update-check + caps render rate at 10 FPS.

---

## Install

### Windows (recommended)

1. Grab the latest `champ-assistant-windows.zip` from
   [GitHub Releases](https://github.com/dedlich/lolchecker/releases).
2. **Add the extracted folder to Windows Defender's exclusion list** before extracting
   (Defender quarantines the bundled `python311.dll` otherwise — symptom: "Failed to
   load Python DLL" on launch). One-time setup.
3. Extract the zip. Run `champ-assistant.exe`.

### From source (any platform)

```bash
git clone https://github.com/dedlich/lolchecker.git
cd lolchecker
python -m venv .venv
. .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
champ-assistant
```

LCDA / LCU integration only works on Windows during a real match. From source on macOS
or Linux you can run the UI in demo mode:

```bash
champ-assistant --dry-run --fixture tests/fixtures/sessions/04_my_turn_top.json
champ-assistant --demo-recommendations
```

---

## Configure

### Riot API key (recommended for full features)

Per-role winrates, behavior tags, and rank badges all read the Riot Web API. Without a
key the app still works — counter / pick / ban / build features don't depend on it —
but the loading-screen surface stays mostly empty.

1. Get a key at [developer.riotgames.com](https://developer.riotgames.com) (dev key
   rotates every 24h; personal-use app key is permanent).
2. Open Settings (⚙ in the title bar) → API Keys → paste + select your region.

### Hotkeys

Settings → Hotkeys. Default actions (rebindable):

| Action            | Default        |
| ----------------- | -------------- |
| Toggle overlay    | Ctrl + Alt + L |
| Lock/unlock drag  | Ctrl + Alt + K |
| Reset positions   | Ctrl + Alt + R |
| Reset full layout | Ctrl + Alt + F |
| Toggle scoreboard | Ctrl + Alt + B |

### Optional services

Each toggleable in Settings:

* **Diagnostics logging** — CPU / RAM / FPS every 10s to `app.log`. On.
* **Telemetry** — local-only JSONL UI/state-transition log. On.
* **Update check** — daily GitHub Releases poll. On.
* **Auto camp detection** (vision, Stage A) — Windows-only, color-heuristic. Off
  (experimental).
* **Scoreboard detection** (vision) — drives the gold-diff overlay's TAB-only gating.
  On.
* **Low Resource Mode** — master switch that overrides all of the above + caps render
  rate. Off.

---

## CLI flags

| Flag                       | Effect                                                      |
| -------------------------- | ----------------------------------------------------------- |
| `--dry-run --fixture PATH` | Replay a saved session payload; no LCU connection needed    |
| `--cycle --interval 5`     | Cycle through every fixture every N seconds (with dry-run)  |
| `--stress --rate 10`       | Emit randomized state updates at N Hz (load testing)        |
| `--demo-recommendations`   | Pre-fill the recommendation panel with example output       |
| `--log-level DEBUG`        | Verbose logging                                             |

---

## Roadmap

The strategy charter (`memory/project_strategy_charter.md`) has the long-form view.
Short version:

* **C — Reliability** — done. crash_report, safe_mode, state_validator,
  health_monitor, lifecycle.
* **A — Performance** — done. performance_monitor, render_scheduler, lazy init,
  Low Resource Mode. A2 (bottleneck removal) waits on production data.
* **B — Smartest** — B1-B5 implemented as 57 rules in the decision engine
  (gank window, lane MIA, tilt detection, recall windows, bounty matrix,
  objective setup, teamfight outcome, etc.). See [RULES.md](RULES.md) for
  the full catalog. Per-rule timing + activation telemetry shipped for
  live tuning. Further coaching depth is gated on data sources LCDA
  doesn't expose (enemy spell cooldowns, ward state, map coordinates).
* **Loading-screen detail view** — pipeline + rendering done. Full Porofessor-style
  fullscreen card view is the next sizable IA shift.

Out-of-scope for safety / Riot ToS reasons: keyboard hooks, memory reading, OCR of
in-game text, automated chat input, anything that triggers Vanguard.

---

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the longer system design write-up
(subsystem boundaries, data flow, design tokens, test strategy). Quick start:

```
src/champ_assistant/
├── __main__.py            # entry point — assembles everything
├── app.py                 # ChampAssistant orchestrator
├── advisor/               # picks / bans / build adapter / decision engine
├── lcu/                   # League Client lockfile + WS + API
├── lcda/                  # Live Client Data API (in-game JSON poll)
├── data/                  # DataDragon + builds.json + counters / tiers / tags
├── profiling/             # Riot Web API client + EnemyProfile composer
├── ui/                    # Qt widgets + design tokens
├── vision/                # mss + numpy color-heuristic detectors (Windows-only)
├── lifecycle.py           # ordered startup / reverse shutdown
├── crash_report.py        # global excepthook + JSON dumps
├── health_monitor.py      # per-service failure tracking + auto-restart
├── state_validator.py     # invariant detection
├── performance_monitor.py # phase-timestamp boot trace
└── ...
```

---

## License

Proprietary — see `pyproject.toml`. Personal / single-user use; no redistribution.

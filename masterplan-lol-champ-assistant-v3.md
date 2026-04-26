# Masterplan: LoL Champ Select Assistant (v3)

> Entwicklung auf macOS + VSCode → Deployment auf Windows, parallel zum Spiel.
> Performant, sicher, **crash-resistent**, mit voller Test-Abdeckung.

-----

## 1. Ziele & Constraints

**Kern-Features (MVP)**

1. **Counter-Anzeige pro Gegner-Champ** – Wer countert ihn, *wie* (Lane-Phase, Spike, Items, Trade-Pattern)
1. **Team-Pick-Empfehlungen** – Beste Picks für *dein* Team (Synergie, fehlende Rollen)
1. **Live-Updates** während Champ Select via LCU WebSocket

**Out of Scope (MVP)**: In-Game Overlay, Builds, Match-History

**Rahmen**

- Dev: macOS + VSCode
- Ziel: Windows 10/11
- Läuft parallel zum Spiel ohne FPS-Impact (<2% CPU, <100 MB RAM)
- **Niemals abstürzen** – immer graceful degradation
- **Niemals den League-Client crashen** – read-only, defensive Connection-Behandlung
- Volle Test-Abdeckung für alle Kern-Module

-----

## 2. UI/UX-Spec (gekürzt – siehe v2 für volle Details)

Single Window ~420×640 px, Frameless + AlwaysOnTop, Dark Mode.
Two Sections: Enemy Team (mit Inline-Expand für Counter-Details) + Your Team Picks.
Status-Bar unten zeigt Connection-State. Hotkeys: `Cmd/Ctrl+H` (hide), `Cmd/Ctrl+R` (refresh).

**Design-Tokens**:

```python
BG_PRIMARY    = "#0F1419"   ; BG_SECONDARY = "#1A1F26"
TEXT_PRIMARY  = "#E8EAED"   ; TEXT_MUTED   = "#8B95A1"
ACCENT        = "#4A9EFF"   ; BORDER       = "#2A3038"
TIER_S_PLUS   = "#FF6B9D"   ; TIER_S       = "#FFB84A"   ; TIER_A = "#7FCC7F"
FONT          = "Inter, -apple-system, Segoe UI, sans-serif"
SPACING_GRID  = 8           ; RADIUS = 6
```

-----

## 3. Tech Stack

**Sprache: Python 3.11+**

|Zweck         |Lib                                                           |
|--------------|--------------------------------------------------------------|
|Async HTTP    |`httpx`                                                       |
|WebSocket     |`websockets`                                                  |
|Datenmodelle  |`pydantic` v2                                                 |
|Claude API    |`anthropic`                                                   |
|UI            |`PyQt6`                                                       |
|Qt + asyncio  |`qasync`                                                      |
|Caching       |`diskcache`                                                   |
|Secrets       |`keyring`, `python-dotenv`                                    |
|Logging       |`structlog` (struktiert, keine Credential-Leaks)              |
|Tests         |`pytest`, `pytest-asyncio`, `respx`, `pytest-qt`, `hypothesis`|
|Coverage      |`pytest-cov`                                                  |
|Soak/Profiling|`py-spy`, `memray`, `tracemalloc`                             |
|Build         |`PyInstaller`                                                 |

-----

## 4. Crash Prevention & Windows-Stabilität

### 4.1 Global Exception Handler

**Niemals** unhandled exceptions die App killen lassen.

```python
# src/champ_assistant/safety.py
import sys, logging, traceback
from PyQt6.QtCore import QObject, pyqtSignal

class CrashHandler(QObject):
    error_occurred = pyqtSignal(str)

    def install(self):
        sys.excepthook = self._handle
        # Auch für asyncio:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(self._handle_async)

    def _handle(self, exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logging.error("Uncaught exception", extra={"trace": msg})
        self.error_occurred.emit(str(exc_value))
        # NICHT sys.exit() aufrufen!

    def _handle_async(self, loop, context):
        logging.error("Async exception", extra={"context": str(context)})
        self.error_occurred.emit(context.get("message", "Async error"))
```

### 4.2 Windows-spezifische Pitfalls

|Problem                     |Lösung                                                                      |
|----------------------------|----------------------------------------------------------------------------|
|Pfade mit Backslashes       |**Immer** `pathlib.Path`, nie String-Concat                                 |
|Unicode-Usernames (Ä/Ö/Ü)   |UTF-8 explizit setzen, `pathlib` nutzt’s korrekt                            |
|`%LOCALAPPDATA%` fehlt      |Fallback auf `Path.home() / "AppData/Local"`                                |
|Asyncio-Loop-Policy         |`ProactorEventLoop` ist Default ab Py 3.8 – passt für `qasync`              |
|HiDPI-Skalierung            |`QApplication.setHighDpiScaleFactorRoundingPolicy(...)` vor `QApplication()`|
|Sleep/Wake → WebSocket tot  |Heartbeat-Ping alle 30s, bei Timeout reconnect                              |
|File-Locking unter Windows  |Lockfile nur **lesen, dann sofort schließen** (Context Manager)             |
|Antivirus blockt PyInstaller|Nuitka als Backup, Code Signing langfristig                                 |
|Multi-Monitor + DPI         |Position relativ zum Primary Screen, mit Bounds-Check                       |

### 4.3 Auto-Recovery-Strategien

**LCU-Verbindung**

```python
async def lcu_connect_with_retry():
    backoff = 1.0
    while True:
        try:
            await connect_to_lcu()
            backoff = 1.0  # Reset bei Erfolg
        except (LockfileNotFound, ConnectionRefused):
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 30.0)  # Cap bei 30s
        except Exception as e:
            logging.warning("LCU connect failed", extra={"err": str(e)})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 30.0)
```

**WebSocket**

- Heartbeat alle 30s
- Bei Timeout/Close: sauber schließen, neu verbinden
- Kein “Crash” der App, nur State → “Reconnecting…”

**Claude API**

- Bei Fehler: Feature stumm degradieren, kein Crash
- “AI Explain”-Button zeigt dann Error-Toast, App läuft weiter

**Lockfile gone (Client closed)**

- State → “Waiting for League Client…”
- Polling-Loop wartet auf Wiedererscheinen

### 4.4 Resource Management

```python
# Beispiel: alle Tasks tracken, sauberer Shutdown
class TaskManager:
    def __init__(self):
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def shutdown(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
```

- HTTP-Clients als Context Manager (`async with httpx.AsyncClient() as c:`)
- Qt-Widgets korrekt parented (Memory-Management via Parent-Child)
- Logging-Handler bei Shutdown flushen

### 4.5 Defensive Defaults

- **Timeouts überall**: HTTP 5s, WebSocket-Operations 10s, Claude 15s
- **Max Retries**: 3 für API-Calls, dann Fehler an UI
- **Circuit Breaker** für Claude API: nach 3 Fehlern in Folge → Feature 5 min aus
- **Memory-Cap**: Bei >150 MB RSS → Warning loggen, Caches leeren
- **Logging ohne Credentials**: Lockfile-Password wird in `__repr__` maskiert

-----

## 5. Testing-Strategie

### 5.1 Test-Pyramide

```
        ┌─────────┐
        │   E2E   │  10%   Manual auf Windows + Dry-Run
       ┌┴─────────┴┐
       │Integration│  20%   Mock-LCU-Server, Mock-Claude
      ┌┴───────────┴┐
      │   Unit Tests │  70%  Pure Functions, Parser, Modelle
      └──────────────┘
```

**Coverage-Ziele**:

- `lcu/lockfile.py`: 100%
- `advisor/*`: 95%+
- `data/*`: 90%+
- `ui/*`: Smoke-Tests, kein Coverage-Ziel

### 5.2 Unit Tests (Übersicht)

|Modul                   |Was getestet wird                                                       |
|------------------------|------------------------------------------------------------------------|
|`lockfile.py`           |Parsing, Path-Resolution Win/Mac, Unicode-Pfade, Missing-File, Corrupted|
|`lcu/client.py`         |Auth-Header, TLS-Skip, Timeout-Handling, Retry-Logic                    |
|`lcu/events.py`         |WS-Reconnect, Heartbeat, Event-Filter                                   |
|`data/datadragon.py`    |Patch-Detection, Cache-Hit/Miss, Network-Error-Fallback                 |
|`data/models.py`        |Pydantic-Validation, Edge-Cases (leere Teams, fehlende Roles)           |
|`advisor/counters.py`   |Counter-Lookup, Role-Filter, Empty-Matrix-Edge-Case                     |
|`advisor/composition.py`|Gap-Detection für alle Comp-Typen                                       |
|`advisor/picks.py`      |Scoring-Algorithmus, Tiebreaker, Score-Range                            |
|`advisor/claude.py`     |Cache-Hit, Cache-Miss, API-Error-Fallback                               |
|`safety.py`             |Exception-Handler triggert UI-Signal, kein sys.exit                     |

### 5.3 Property-Based Tests (Hypothesis)

Für die Advisor-Logik – fängt Edge-Cases, an die du nicht denkst:

```python
from hypothesis import given, strategies as st

@given(
    enemies=st.lists(champion_strategy(), min_size=1, max_size=5),
    role=st.sampled_from(["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]),
)
def test_find_counters_never_returns_enemy(enemies, role):
    """Counters dürfen nie den Gegner selbst enthalten."""
    for enemy in enemies:
        counters = find_counters(enemy, role, COUNTER_MATRIX)
        assert enemy.id not in [c.id for c in counters]

@given(team=team_strategy(), enemies=team_strategy())
def test_suggest_picks_always_valid(team, enemies):
    """Vorschläge müssen immer gültige Champions sein."""
    picks = suggest_picks("TOP", team, enemies, ALL_GAPS, TIER_LIST)
    for p in picks:
        assert p.champion_id in ALL_CHAMPIONS
        assert 0 <= p.score <= 100
```

### 5.4 Dummy-Daten-Katalog

`tests/fixtures/sessions/` – realistische Champ-Select-Sessions für Replay:

|Datei                      |Szenario                                               |
|---------------------------|-------------------------------------------------------|
|`01_ban_phase.json`        |Mitten in der Ban-Phase, noch keine Picks              |
|`02_early_picks.json`      |2 Picks gemacht (Blue: Top, Red: Jungle)               |
|`03_mid_picks.json`        |5 Picks, 5 offen                                       |
|`04_my_turn_top.json`      |Du bist dran, spielst Top                              |
|`05_my_turn_jungle.json`   |Du bist dran, spielst Jungle                           |
|`06_final_lock.json`       |Alle gepickt, kurz vor Lock                            |
|`07_aram.json`             |ARAM-Modus (anderes Schema!)                           |
|`08_swap_request.json`     |Trade-Request läuft                                    |
|`09_dodge.json`            |Jemand hat dodged                                      |
|`10_disconnected_ally.json`|Mitspieler hat Disconnect                              |
|`11_partial_data.json`     |Unvollständige Daten (Network-Hiccup simulieren)       |
|`12_corrupt.json`          |Kaputtes JSON (Parser-Robustheit)                      |
|`13_unknown_champion.json` |Champion-ID, die nicht in Data Dragon ist (neuer Champ)|

**Wie befüllen?**

- Recording-Script: `python scripts/record_session.py` läuft mit echtem Client und schreibt jede Session als JSON
- Manuell anpassen für Edge-Cases (z.B. corrupt, partial)

### 5.5 Integration Tests

**Mock-LCU-Server** (mit `aiohttp`):

```python
# tests/integration/test_lcu_full_flow.py
async def test_full_champ_select_flow(mock_lcu_server, qtbot):
    """End-to-end: LCU sendet Events → UI updatet."""
    server = mock_lcu_server
    server.queue_session("01_ban_phase.json")
    server.queue_session("02_early_picks.json")
    server.queue_session("06_final_lock.json")

    app = ChampAssistant(lcu_url=server.url)
    await app.start()

    # Warte bis UI synct
    await qtbot.wait_until(lambda: app.ui.has_data(), timeout=2000)

    # Verifiziere
    assert app.ui.enemy_count() == 5
    assert app.ui.suggestion_count() >= 3

    await app.shutdown()
```

### 5.6 Dry-Run-Modus (Killer-Feature für Mac-Dev!)

**Volle App auf Mac testbar, ohne League-Client.**

```bash
# Lädt UI mit Fixture, kein LCU
python -m champ_assistant --dry-run --fixture tests/fixtures/sessions/04_my_turn_top.json

# Cycle-Modus: wechselt alle 5s zur nächsten Fixture
python -m champ_assistant --dry-run --cycle --interval 5

# Stress-Modus: zufällige State-Updates 10x/Sekunde
python -m champ_assistant --dry-run --stress --rate 10
```

Implementation:

```python
# src/champ_assistant/__main__.py
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--cycle", action="store_true")
    parser.add_argument("--stress", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        lcu_source = FixtureLcuSource(args.fixture, cycle=args.cycle, stress=args.stress)
    else:
        lcu_source = RealLcuSource()

    app = ChampAssistant(lcu_source=lcu_source)
    app.run()
```

Nutzen:

- **Mac-Dev**: Du siehst dein UI live mit echten Daten ohne League
- **CI**: Smoke-Test ob App startet und UI rendert
- **Demo**: Screenshots/Videos einfach machen

### 5.7 Soak-Tests (Stabilität über Zeit)

```bash
# Läuft 4 Stunden mit zufälligen State-Changes,
# checkt jede Minute RAM und Task-Count
pytest tests/soak/test_longevity.py --duration=4h
```

Asserts:

- RAM-Wachstum < 10 MB über 4h
- Task-Count bleibt stabil (kein Leak)
- Keine unhandled Exceptions im Log
- UI bleibt responsive (Event-Loop-Lag < 100ms)

### 5.8 Windows-spezifische CI-Tests

`.github/workflows/test.yml`:

```yaml
jobs:
  test-mac:
    runs-on: macos-latest
    steps: [..., pytest]

  test-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -e ".[dev]"
      - run: pytest tests/ -v --cov
      - run: pytest tests/windows_specific/ -v  # Path-Tests, etc.
```

Tests in `tests/windows_specific/`:

- Lockfile-Pfad-Resolution mit `%LOCALAPPDATA%`
- Unicode-Path-Handling
- Asyncio-Loop-Policy
- HiDPI-Setup (kann nur partiell automatisiert werden)

### 5.9 Manual E2E auf Windows

**Pre-Release-Checklist** (`docs/manual_test_checklist.md`):

- [ ] App startet ohne Client → zeigt “Waiting…”
- [ ] Client startet → App connected innerhalb 5s
- [ ] Champ Select beginnt → Daten erscheinen <1s
- [ ] Gegner wird gepickt → Counter-Anzeige updates live
- [ ] Eigener Pick → Empfehlungen aktualisieren
- [ ] Champ Select endet → App geht zurück zu “Waiting…”
- [ ] Client wird geschlossen → App zeigt “Disconnected”, crasht nicht
- [ ] Client neu gestartet → App reconnected automatisch
- [ ] Windows in Sleep → Wake: Reconnect funktioniert
- [ ] AI Explain ohne Internet → Error-Toast, App läuft weiter
- [ ] AI Explain mit gültigem Key → Antwort erscheint <3s
- [ ] App läuft 1h im Background → RAM stabil (Task-Manager)
- [ ] Während Spiel: keine FPS-Drops (mit Riot Performance-Overlay prüfen)
- [ ] Hotkeys funktionieren
- [ ] App schließt sauber (kein Zombie-Prozess)

-----

## 6. Architektur (gekürzt – siehe v2)

Single-Process Python-App mit `qasync`. LCU-Watcher (asyncio) → State Store → Advisor Engine → Qt-UI. Static Data (Champions, Counter-Matrix, Tier-Liste, Tags) aus JSON im Repo. Claude API on-demand.

-----

## 7. Sicherheit (gekürzt – siehe v2)

- LCU-Credentials nie loggen, `__repr__` maskiert
- API-Keys in `keyring` (Windows Credential Manager)
- TLS-Verify aus, **nur** für 127.0.0.1
- Riot ToS: nur lesend, keine Automation

-----

## 8. Performance (gekürzt – siehe v2)

WebSocket statt Polling, State-Diff vor UI-Update, Static Data einmalig laden, Claude on-demand only. Ziel: <2% CPU, <100 MB RAM.

-----

## 9. Projekt-Struktur

```
lol-champ-assistant/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── data/
│   ├── counters.json
│   ├── tiers.json
│   ├── tags.json
│   └── strategies/*.md
├── src/champ_assistant/
│   ├── __init__.py
│   ├── __main__.py            # Entry + CLI args (--dry-run etc.)
│   ├── config.py
│   ├── safety.py              # CrashHandler, Watchdog
│   ├── tasks.py               # TaskManager
│   ├── lcu/
│   │   ├── lockfile.py
│   │   ├── client.py
│   │   ├── events.py
│   │   └── sources.py         # RealLcuSource + FixtureLcuSource
│   ├── data/
│   │   ├── datadragon.py
│   │   ├── loader.py
│   │   └── models.py
│   ├── advisor/
│   │   ├── counters.py
│   │   ├── composition.py
│   │   ├── picks.py
│   │   └── claude.py
│   └── ui/
│       ├── overlay.py
│       ├── enemy_row.py
│       ├── pick_card.py
│       ├── styles.py
│       └── widgets.py
├── tests/
│   ├── unit/
│   │   ├── test_lockfile.py
│   │   ├── test_lcu_client.py
│   │   ├── test_lcu_events.py
│   │   ├── test_models.py
│   │   ├── test_counters.py
│   │   ├── test_composition.py
│   │   ├── test_picks.py
│   │   ├── test_claude.py
│   │   └── test_safety.py
│   ├── integration/
│   │   ├── test_lcu_full_flow.py
│   │   ├── test_advisor_pipeline.py
│   │   └── conftest.py        # Mock-LCU-Server fixture
│   ├── property/
│   │   ├── test_counters_properties.py
│   │   └── test_picks_properties.py
│   ├── windows_specific/
│   │   ├── test_paths.py
│   │   └── test_unicode_paths.py
│   ├── soak/
│   │   └── test_longevity.py
│   ├── ui/
│   │   ├── test_overlay_smoke.py
│   │   └── test_dry_run.py
│   └── fixtures/
│       ├── sessions/          # 13 JSON-Fixtures (siehe 5.4)
│       ├── lockfiles/
│       └── champions.json
├── scripts/
│   ├── record_session.py      # Echte Sessions als Fixture aufzeichnen
│   ├── update_tiers.py
│   ├── update_counters.py
│   └── build_windows.spec
├── docs/
│   └── manual_test_checklist.md
└── .github/workflows/
    ├── test.yml               # Mac + Windows Tests
    └── build.yml              # Windows-Build
```

-----

## 10. Entwicklungs-Workflow (Mac → Windows)

### Phase 0: Setup (Mac)

```bash
mkdir lol-champ-assistant && cd lol-champ-assistant
python3.11 -m venv .venv && source .venv/bin/activate
pip install httpx websockets pydantic anthropic pyqt6 qasync \
            diskcache python-dotenv keyring structlog \
            pytest pytest-asyncio pytest-qt pytest-cov \
            respx hypothesis aiohttp
```

- `pyproject.toml` mit Ruff + Mypy + pytest config
- VSCode: Python, Ruff, Pylance, Even Better TOML

### Phase 1: Safety-Layer zuerst!

- `safety.py`: Global Exception Handler
- `tasks.py`: Task-Manager
- Tests dafür → grün

### Phase 2: LCU-Layer

- Lockfile-Parser + Tests (inkl. Unicode-Pfade)
- LCU-Client mit `respx`
- WS-Subscriber mit Mock-Server
- `FixtureLcuSource` für Dry-Run
- Tests → grün

### Phase 3: Data Layer

- Data Dragon Loader + Cache
- Initial JSON-Daten (Counter-Matrix Top 30, Tier-Liste, Tags)
- Pydantic-Modelle
- Tests → grün

### Phase 4: Advisor

- `find_counters`, `analyze_composition`, `suggest_picks`
- Unit Tests + Property-Based Tests
- Coverage > 95%

### Phase 5: UI

- Layout aus Spec, Mock-Daten
- Smoke-Tests mit `pytest-qt`
- Dry-Run-Mode lauffähig auf Mac

### Phase 6: Integration

- Alles verdrahten
- Integration-Tests grün
- Soak-Test (1h auf Mac als Sanity-Check)

### Phase 7: Claude On-Demand

- Anthropic SDK
- Caching
- Circuit Breaker

### Phase 8: Windows-Build

- GitHub Actions Windows-Runner
- PyInstaller-Spec
- Artifact Download

### Phase 9: Live-Test auf Windows

- Manual Checklist abarbeiten
- Profiling mit `py-spy` und `memray`
- Soak-Test 4h

-----

## 11. Logging-Strategie

```python
import structlog

log = structlog.get_logger()

# Mit Credential-Maskierung
log.info("lcu_connected", port=12345, password="***")  # NIEMALS echtes PW

# Strukturiert für späteren Debug
log.debug("event_received", event_type="champ_select", state_change=diff)
log.warning("reconnect", reason="timeout", attempt=3)
log.error("api_failure", endpoint="/lol-champ-select/v1/session", err=str(e))
```

- Default-Level: `INFO`
- File-Logging: rotating log unter `%LOCALAPPDATA%\ChampAssistant\logs\`
- Kein Console-Spam (App ist windowed)
- Crash-Logs separat in `crashes/` mit Full Traceback

-----

## 12. Distribution

PyInstaller One-File für dich. Inno Setup + Code Signing für andere.
Keine Auto-Updates im MVP – manueller Download von GitHub Releases.

-----

## 13. Risiken & Mitigation

|Risiko                 |Mitigation                                       |
|-----------------------|-------------------------------------------------|
|Riot patcht LCU        |Versions-Check beim Start, Community Dragon Docs |
|PyInstaller AV-Flags   |Nuitka-Backup, Code Signing                      |
|Memory-Leak            |Soak-Tests, `tracemalloc`, Cap-Warning           |
|Race Conditions Startup|`asyncio.Lock`, deterministische Init-Reihenfolge|
|Counter-Daten veralten |Update-Skript + Patch-Banner in UI               |
|Cross-Compile          |GitHub Actions, niemals lokal von Mac            |
|Qt-Version-Mismatch    |PyQt6 fest pinnen in `pyproject.toml`            |

-----

## 14. Start im neuen Kontext

Kopier diesen Masterplan v3 in den neuen Claude-Chat und sag:

> “Hier ist mein Masterplan v3. Ich bin auf macOS mit VSCode, Python 3.11+ ist installiert.
> Lass uns mit **Phase 0** starten: Erstelle die initiale Projekt-Struktur
> (`pyproject.toml` mit allen Dev/Test-Dependencies, `.gitignore`, `.env.example`,
> Modul-Skelette unter `src/champ_assistant/` inkl. `safety.py` und `tasks.py`,
> `data/`-Ordner mit JSON-Stubs, Test-Struktur unter `tests/` mit allen Unterordnern)
> und eine minimale `__main__.py` mit `--dry-run`-Argument-Parsing.
> 
> Danach gehen wir Phase für Phase durch. **Wichtig**: Jede Phase wird durch
> Unit Tests abgesichert, bevor wir zur nächsten gehen. Property-Based Tests für die
> Advisor-Module. Dry-Run-Modus muss früh funktionieren, damit ich auf dem Mac
> entwickeln kann.”

-----

## 15. Quick-Reference: API-Endpoints

|Was                  |Endpoint                                                                |
|---------------------|------------------------------------------------------------------------|
|Champ Select Session |`GET /lol-champ-select/v1/session`                                      |
|Champ Select Events  |WS: `OnJsonApiEvent_lol-champ-select_v1_session`                        |
|Current Summoner     |`GET /lol-summoner/v1/current-summoner`                                 |
|Data Dragon Versions |`https://ddragon.leagueoflegends.com/api/versions.json`                 |
|Data Dragon Champions|`https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion.json`|

-----

**Viel Erfolg! 🎯**
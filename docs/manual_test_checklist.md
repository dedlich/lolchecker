# Manual Test Checklist — Pre-Release on Windows

Run through this checklist on a Windows machine before tagging a release.
Source: masterplan v3 §5.9.

## Setup

- [ ] Download the artifact from the latest **build-windows** GitHub Actions run
- [ ] Unzip; the bundle is the folder `champ-assistant/` containing
      `champ-assistant.exe` plus Qt + Python runtime
- [ ] Optional: copy `.env.example` next to the exe as `.env` and set
      `ANTHROPIC_API_KEY` (only needed once the AI Explain UI button ships)
- [ ] First launch: Defender / SmartScreen may flag the unsigned exe — click
      "More info → Run anyway" once. Subsequent launches are quiet.

## Lifecycle

- [ ] App starts with **no League client running** → status bar shows
      "Waiting for League Client…", no crash
- [ ] Start the League client → app reaches "Connected" within 5 s
- [ ] Enter champ select → enemy team + suggestions appear in &lt; 1 s
- [ ] Enemy locks a champion → counter list updates live for that slot
- [ ] You declare a role → suggestions for your role refresh
- [ ] Champ select ends (game starts or dodge) → app returns to "Waiting…"
- [ ] **Close the League client mid-session** → status bar shows
      "Disconnected", app does NOT crash
- [ ] Start the client again → app auto-reconnects within 5 s

## Resilience

- [ ] **Sleep / wake** Windows during champ select → on wake, the WS
      reconnects and state catches up (no zombie "Connecting…" forever)
- [ ] Pull the network cable mid-session → status flips to
      "Reconnecting…", restoring the cable recovers within 30 s
- [ ] Open + close champ select 5 times in a row → no memory creep,
      no orphan tasks
- [ ] App runs **1 hour idle in tray** → RAM stable in Task Manager
      (&lt; ±5 MB drift), CPU near 0 %

## Performance

- [ ] **In-game with the app running**: open Riot's performance overlay
      (`Ctrl + F`) and play a match. CPU contribution from
      `champ-assistant.exe` &lt; 2 %; no FPS drop visible
- [ ] Multi-monitor: window stays on the primary screen across docking
      events; doesn't fly off-screen on resolution changes

## UX

- [ ] Hotkey `Ctrl + H` hides / shows the window
- [ ] Hotkey `Ctrl + R` forces a refresh (you see the suggestions
      re-render even if the session payload is unchanged)
- [ ] Window is frameless + always-on-top once `frameless=True` is wired
      into `__main__` (currently off by default — flip when ready)
- [ ] Drag the title area: window moves smoothly (Qt frameless windows
      need a custom mousePressEvent — note as TODO if not yet)

## AI Explain (Phase 7+ surface — once UI button ships)

- [ ] No `ANTHROPIC_API_KEY` set → "AI Explain" button shows a friendly
      error toast, app continues
- [ ] Valid key + matchup with internet → response renders &lt; 3 s
- [ ] Identical matchup queried twice in a row → second response is
      instantaneous (cache hit)
- [ ] Disconnect from internet, click AI Explain 3 times → after the
      third failure, button is disabled with "Try again in 5 min"
      (circuit breaker open)

## Shutdown

- [ ] Click the close button → process exits cleanly within 1 s, no
      "ghost" entry in Task Manager
- [ ] Repeat 5 times: open / close cycle leaves no `champ-assistant.exe`
      processes lingering

---

When everything above is checked, the build is ready to tag.

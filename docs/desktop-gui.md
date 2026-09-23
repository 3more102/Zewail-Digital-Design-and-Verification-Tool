# Desktop Debug Studio

ZDDV v1.1 introduces a local Tk/ttk Debug Studio that displays persisted verification evidence through the same core data model used by the CLI.

## Launch

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 200
```

The first desktop slice shows recent simulation runs, normalized failure groups, assertion statistics, the newest normalized coverage evidence, and the latest formal and UVM snapshots. Coverage selection supports both point-based coverage snapshots and percentage-native score snapshots used by commercial simulator adapters.

## Trust boundary

The GUI is display-only. Refreshing it does not start simulations, invoke an AI provider, stage or apply generated artifacts, sign releases, or edit RTL/testbench sources. Verification status shown in the window is taken from persisted ZDDV evidence; the GUI does not infer additional pass/fail claims.

The desktop layer intentionally reuses the existing storage, failure-grouping, coverage, formal, and UVM APIs rather than maintaining a second verification database or duplicating result semantics.

## Current scope

This foundation does not yet provide interactive source editing, waveform plotting, protocol drill-down, or project-mutating actions. Those can be added behind the same evidence and review boundaries used by the CLI.

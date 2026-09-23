# Desktop Debug Studio

ZDDV v1.1 introduces a local Tk/ttk Debug Studio that displays persisted verification evidence through the same core data model used by the CLI.

## Launch

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 200
```

The desktop shows recent simulation runs, normalized failure groups, assertion statistics, the newest normalized coverage evidence, the latest formal and UVM snapshots, plus read-only Sources and Hierarchy panes. Coverage selection supports both point-based coverage snapshots and percentage-native score snapshots used by commercial simulator adapters.

## Trust boundary

The GUI is display-only. Refreshing it does not start simulations or elaboration, invoke an AI provider, stage or apply generated artifacts, sign releases, or edit RTL/testbench sources. Verification status shown in the window is taken from persisted ZDDV evidence; the GUI does not infer additional pass/fail claims. The Sources pane rebuilds the deterministic source index in memory only. The elaborated hierarchy is shown only when an existing `.zddv/design/elaborated.json` matches the current project, top, and simulator.

The desktop layer intentionally reuses the existing storage, failure-grouping, coverage, formal, and UVM APIs rather than maintaining a second verification database or duplicating result semantics.

## Source and hierarchy navigation

The **Sources** pane groups each configured HDL source file with the design units discovered by the existing deterministic source-index parser. File metadata includes line/byte counts and SHA-256 fingerprints; design units retain kind and source-line ranges.

The **Hierarchy** pane renders the current source-level top hierarchy and, when already captured by `zddv elaborate`, the persisted simulator-elaborated instance hierarchy. A missing elaborated artifact is shown explicitly as `NOT_CAPTURED`; stale project/top/simulator identity is reported as a mismatch rather than silently displayed as current evidence.

## Current scope

The desktop still does not provide source editing, waveform plotting/probing, protocol drill-down, or project-mutating actions. Waveform/probe integration is the next planned navigation slice and will reuse the existing waveform-index/cross-probe APIs without automatic simulation execution.

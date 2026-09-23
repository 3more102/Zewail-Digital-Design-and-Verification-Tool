# Desktop Debug Studio

ZDDV v1.1 provides a local Tk/ttk Debug Studio over persisted verification evidence and the deterministic in-memory design index.

## Launch

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 200
```

The desktop view includes recent runs, normalized failure groups, source files and source-level hierarchy, persisted simulator-elaborated hierarchy, assertion/coverage/formal/UVM evidence, and waveform navigation for recorded simulation artifacts.

## Waveform navigation

The **Waveform** tab selects the newest recorded run whose waveform artifact still exists. For VCD evidence, ZDDV parses the declaration header in memory and lists signal paths, widths, and VCD types. Select one signal and optionally enter integer start/end times plus a maximum number of changes, then use **Probe selected** to inspect only that signal's recorded value changes.

This path reuses the existing in-memory VCD index/probe APIs. It does not write the normal `.zddv/waveforms/*.json` index or probe artifacts. FST evidence remains metadata-only until a verified FST parser or converter adapter is available.

## Trust boundary

The GUI does not start simulations or elaboration, invoke AI providers, apply generated artifacts, or edit RTL/testbench files. Waveform browsing reports only values present in the recorded artifact. A displayed signal transition is waveform evidence, not a new verification conclusion.

## Next desktop increments

Planned work includes rendered waveform cursors, waveform-to-source/cross-probe selection, deeper assertion/UVM/formal drill-down, and explicitly review-gated project actions.

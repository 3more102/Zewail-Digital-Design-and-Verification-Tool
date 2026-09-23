# Desktop Debug Studio

ZDDV v1.1 provides a local Tk/ttk Debug Studio over persisted verification evidence.

## Launch

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 200
```

The current desktop view includes run summaries, recent runs, normalized failure groups,
assertion/coverage/formal/UVM evidence, and waveform navigation for recorded simulation
artifacts.

## Waveform navigation

The **Waveform** tab selects the newest recorded run whose waveform artifact still
exists. For VCD evidence, ZDDV parses the declaration header in memory and lists the
recorded signal paths, widths, and VCD types. A selected signal can be probed over an
optional integer start/end time window with an explicit per-signal change limit.

This desktop path calls the existing in-memory VCD index/probe APIs. It does not write
the normal `.zddv/waveforms/*.json` index or probe artifacts. FST evidence remains
metadata-only until an FST parser/converter adapter is available.

## Trust boundary

The GUI does not start simulations, invoke AI providers, apply generated artifacts,
or edit RTL/testbench files. Waveform browsing reports only values present in the
recorded artifact. A displayed signal transition is waveform evidence, not a new
verification conclusion.

## Next desktop increments

Planned work includes source/hierarchy navigation, rendered waveform cursors,
waveform-to-source/cross-probe selection, deeper assertion/UVM/formal drill-down, and
explicitly review-gated project actions.

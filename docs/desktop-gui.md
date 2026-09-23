# Desktop Debug Studio

ZDDV v1.1 provides a local Tk/ttk Debug Studio over persisted verification evidence and deterministic design models.

## Launch

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 200
```

The desktop view includes recent runs, normalized failure groups, source and source-level hierarchy navigation, persisted simulator-elaborated hierarchy evidence, assertion/coverage/formal/UVM summaries, and recorded waveform browsing.

## Waveform navigation

The **Waveform** tab selects the newest persisted run whose recorded waveform still exists. This selection is independent of the recent-run display limit. For VCD evidence, ZDDV parses the declaration header in memory and lists signal paths, widths, and VCD types.

Select one signal, optionally enter integer start/end times and a maximum number of changes, then choose **Probe selected**. The probe streams only the selected signal's recorded VCD changes.

The desktop path calls the existing in-memory waveform index and probe APIs. It does not write the normal `.zddv/waveforms/*.json` index or probe artifacts. FST evidence is shown as metadata-only and is not treated as probeable until a supported FST parser or converter adapter is available.

## Trust boundary

The GUI does not start simulations, invoke AI providers, apply generated artifacts, or edit RTL/testbench files. Waveform transitions are displayed as recorded evidence, not as new verification conclusions.

## Next desktop increments

Planned work includes rendered waveform/cursor views, waveform-to-source cross-probe selection, deeper assertion/UVM/formal drill-down, and explicitly review-gated project actions.

# Desktop Waveform Navigation

The ZDDV Desktop Debug Studio can browse waveform evidence already recorded by simulation runs without starting a simulator or writing new debug artifacts.

## Behavior

The Waveform tab selects the newest recorded run whose waveform file still exists. For VCD files it parses the declaration header in memory and lists signal paths, widths, and VCD types. The selected signal can then be probed over optional integer start/end times with an explicit maximum number of recorded changes.

The tab reuses the same `build_waveform_index`, `probe_vcd_signals`, and cross-probe core APIs used by the CLI, but it does not call the artifact-writing waveform-index or waveform-probe commands. The Desktop tab does not invoke `fst2vcd`; FST therefore remains metadata-only in the Desktop unless a future explicit Desktop adapter is added.

The cross-probe evidence pane shows the resolved hierarchy/source declaration, source-structural driver/load counts, normalized elaborated module-port direction when available, and bounded direct CELL-pin-to-VARREF boundary bindings when persisted evidence is normalized. Missing or unavailable elaborated evidence is shown explicitly and is not inferred.\n\n## Trust boundary

Waveform values are observations from the recorded artifact. The desktop view does not execute verification, edit HDL, invoke AI, apply generated artifacts, or infer a new pass/fail conclusion from a transition.

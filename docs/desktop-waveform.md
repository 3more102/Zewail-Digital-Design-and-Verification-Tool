# Desktop Waveform Navigation

The ZDDV Desktop Debug Studio can browse waveform evidence already recorded by simulation runs without starting a simulator or writing new debug artifacts.

## Behavior

The Waveform tab selects the newest recorded run whose waveform file still exists. For VCD files it parses the declaration header in memory and lists signal paths, widths, and VCD types. The selected signal can then be probed over optional integer start/end times with an explicit maximum number of recorded changes.

The tab reuses the same `build_waveform_index`, `probe_vcd_signals`, and cross-probe core APIs used by the CLI, but it does not call the artifact-writing waveform-index or waveform-probe commands. The Desktop tab does not invoke `fst2vcd`; FST therefore remains metadata-only in this tab.

When normalized elaborated evidence exists, the cross-probe pane keeps source-structural drivers/loads separate from simulator-elaborated evidence. It renders only the trusted `simulator_elaborated_direct_pin_varref` contract, shows bounded exact parent/child endpoints, preserves unsupported complex expressions as evidence without inventing a direct relation, and reports hidden evidence counts when the row limit is exceeded. It also surfaces the core `simulator_elaborated_to_source_structural_correlation` contract only when role semantics remain `source_structural_only`; ambiguous, missing, or unavailable correlations are summarized without being displayed as exact source routes.

## Trust boundary

Waveform values are observations from the recorded artifact. The desktop view does not execute verification, edit HDL, invoke AI, apply generated artifacts, or infer a new pass/fail conclusion from a transition.

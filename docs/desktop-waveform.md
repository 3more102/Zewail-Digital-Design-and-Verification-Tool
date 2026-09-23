# Desktop Waveform Navigation

The ZDDV Desktop Debug Studio can browse waveform evidence already recorded by simulation runs without starting a simulator or writing new debug artifacts.

## Behavior

The Waveform tab selects the newest recorded run whose waveform file still exists. For VCD files it parses the declaration header in memory and lists signal paths, widths, and VCD types. The selected signal can then be probed over optional integer start/end times with an explicit maximum number of recorded changes.

The tab reuses the same `build_waveform_index` and `probe_vcd_signals` core APIs used by the CLI, but it does not call the artifact-writing waveform-index or waveform-probe commands. FST remains metadata-only until a verified parser or converter adapter is available.

When cross-probe returns the trusted `simulator_elaborated_direct_pin_varref` contract, the desktop also renders the core-provided module-boundary driver, load, and unclassified role buckets. Role rows preserve the reported `query_side`, parent/child endpoint, and port direction, are bounded by the existing elaborated-evidence limit, and are not recomputed in the GUI. Unknown future connectivity contracts remain uninterpreted.

When the core also returns the trusted `simulator_elaborated_to_source_structural_correlation` contract with `role_semantics=source_structural_only`, the desktop shows deterministic correlation status counts and one source route only when exactly one MATCHED correlation carries well-typed source-edge, role, and match-basis evidence. Malformed containers, unknown statuses, future contracts, and non-source-structural role semantics fail closed and produce no correlation claim. Duplicate normalized direct-pin endpoints are collapsed only for display; the core evidence is not modified.

## Trust boundary

Waveform values are observations from the recorded artifact. The desktop view does not execute verification, edit HDL, invoke AI, apply generated artifacts, or infer a new pass/fail conclusion from a transition.

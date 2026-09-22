# ZDDV Roadmap

## v0.1 — Simulation Foundation

Exit criteria:

- Installable `zddv` CLI.
- Project creation and configuration.
- Source discovery.
- Verilator environment detection.
- Build and simulation.
- Reproducible build/run metadata.
- PASS/FAIL result.
- Waveform artifact capture.
- Self-checking example.
- Unit + integration CI.

## v0.2 — Regression Engine

Current status: v0.2 is feature-complete with SQLite run history, selective rerun, JUnit export, failure-signature grouping, structured lint diagnostics, and an HTML verification dashboard.

- Named tests.
- Seeds and plusargs.
- Regression YAML/TOML.
- Parallel execution.
- Timeouts.
- Selective rerun.
- SQLite result database.
- CLI summary tables.
- JUnit/JSON export.

## v0.3 — Verification Results

Current status: v0.3 is feature-complete with normalized assertion results, code coverage,
functional coverage snapshots/bins, coverage merge and holes, failure clustering, and coverage trends.

- [x] Assertions database.
- [x] Code-coverage ingestion.
- [x] Functional-coverage schema.
- [x] Coverage merge.
- [x] Coverage-hole reporting.
- [x] Failure signatures and clustering.
- [x] Normalized coverage history/trend.

## v0.4 — Debug Studio Core

Current status: deterministic source indexing, source-level hierarchy, VCD waveform
indexing, targeted VCD value-change probing, source-level structural drivers/loads
navigation, waveform-to-source cross-probing, assertion-to-waveform correlation, and
the first protocol transaction reconstruction path (normalized APB traces) are
implemented. Elaborated connectivity and waveform-driven protocol extraction remain planned.

- [x] Design hierarchy (source-level).
- [x] Source indexing.
- [x] Waveform index (VCD scopes/signals; FST artifact metadata).
- [x] Waveform-to-source cross-probing.
- [x] Targeted VCD value-change probing (time-windowed, bounded).
- [x] Drivers/loads navigation (source-level structural evidence).
- [x] Assertion-to-waveform correlation (run/artifact link plus exact-name signal hints).
- [x] Protocol transaction reconstruction (APB normalized-trace foundation).
- [x] Waveform-driven protocol extraction (APB VCD clock-edge sampling).

## v0.5 — Protocol Verification

Current status: normalized-trace analyzers are implemented for APB, AXI4-Lite,
burst-aware AXI4, async-FIFO CDC invariants, and a public-facts-based UCIe FLIT/link-health
foundation. Core AXI4 exclusive-access semantics plus address-sideband width and AxREGION 4KB
consistency checks, reserved AxCACHE encoding validation and B/M/RA/WA attribute decoding,
optional USER-sideband transport evidence, and data-width-aware AxSIZE/WSTRB byte-lane
validation are implemented; exhaustive optional AXI4 sideband/coherency semantics,
structural CDC signoff, and specification-complete UCIe checking remain planned.

- [x] APB normalized-trace reconstruction and protocol checks.
- [x] AXI4-Lite five-channel transaction reconstruction and protocol checks.
- [x] AXI4 burst normalized-trace foundation (IDs, lengths/types, WLAST/RLAST, 4KB rule).
- [x] AXI4 burst VCD extraction with timestamped transaction reconstruction.
- [x] AXI4 exclusive-access size/alignment, response, timing, and observed-pair checking.
- [x] AXI4 AxCACHE/AxPROT/AxQOS/AxREGION width checks and AxREGION 4KB consistency.
- [x] AXI4 reserved AxCACHE encoding validation and B/M/RA/WA attribute decoding.
- [x] AXI4 AWUSER/WUSER/BUSER/ARUSER/RUSER capture and channel-stability checking.
- [x] AXI4 data-width-aware AxSIZE and WSTRB byte-lane legality checks.
- [ ] Exhaustive AXI4 optional-sideband/coherency-adjacent semantics.
- [x] Asynchronous FIFO / CDC-oriented normalized-event invariant checks.
- [x] UCIe public 68B/256B FLIT trace and link-health foundation.
- [ ] Specification-complete UCIe protocol/PHY conformance checking.

Each pack should support reusable checks, assertions, transaction extraction, coverage goals, and debug summaries.

## v0.6 — UVM and Commercial Simulator Adapters

Current status: the simulator-independent UVM report-log ingestion foundation and the
first commercial-simulator adapter foundation are implemented. ZDDV extracts standard UVM
report messages, test names, severity summaries, report IDs, components, source locations, and
timestamps; persists normalized snapshots/messages in SQLite; correlates snapshots with recorded
simulation runs; and exposes `uvm-analyze` plus `uvm-history`. The Questa foundation supports
native compile/run orchestration, seeds/tests/plusargs, timeouts, optional VCD capture, assertion
ingestion, run-linked UVM ingestion, and per-run native UCDB coverage capture. Multi-run UCDB merge plus documented
`vcover report -summary` normalization into ZDDV coverage history are implemented. Ordinary
covergroup bins from `vcover report -cvg -details` are also normalized into the existing
functional-coverage database. Machine-readable XML detail output and complementary zero-hit source/file-line evidence are retained when available. Documented statement/branch rows plus scalar condition/expression FEC detail rows are normalized into the shared coverage-hole model. Standard two-state toggle transition counts are normalized from the native toggle report; multibit FEC layouts, extended-Z toggle transitions, and FSM itemization remain planned.
Phase/objection lifecycle normalization, explicit sequence report-context evidence, normalized sequence state lifecycle ingestion, and normalized sequence-item handshake persistence/history are implemented; automatic sequence/item instrumentation adapters and arbitration priority/fairness reconstruction remain planned.

- [x] Questa execution adapter foundation.
- [x] Questa native per-run UCDB coverage capture.
- [x] Questa UCDB merge and summary-level ZDDV coverage reporting.
- [x] Questa ordinary functional covergroup-bin normalization and functional hole queries.
- [x] Questa detailed XML coverage evidence export.
- [x] Questa complementary zero-hit source-detail evidence export.
- [x] Questa statement/branch plus scalar condition/expression FEC item/source normalization and coverage-hole reporting.
- [x] Questa standard two-state toggle transition item-level normalization and coverage-hole reporting.
- [ ] Questa multibit condition/expression FEC, extended-Z toggle, and FSM item-level normalization.
- [x] VCS execution adapter foundation (build/run, seed/test/plusargs, VCD, assertions, run-linked UVM).
- [x] VCS native per-run coverage database capture.
- [x] VCS multi-run URG merge and report evidence retention.
- [x] VCS normalized URG dashboard score ingestion and percentage-native history snapshots.
- [x] VCS documented global covergroup type/instance covered/expected count ingestion.
- [x] VCS documented module-level line/branch covered/total count ingestion from `modinfo.txt`.
- [x] VCS instance-level line/condition/toggle/branch covered/total ingestion plus separate FSM state/transition/sequence counts from URG HTML detail reports.
- [ ] VCS remaining condition/toggle/FSM module-level count ingestion from `modinfo.txt`.
- [x] Xcelium execution adapter foundation (xrun elaborate/run, seed/test/plusargs, VCD, assertions, run-linked UVM).
- [x] Xcelium native per-run coverage database capture.
- [x] Xcelium multi-run IMC merge/report evidence retention.
- [ ] Xcelium metric normalization into ZDDV coverage history.
- [x] Simulator-independent UVM report/test metadata ingestion and SQLite persistence.
- [x] UVM snapshot-to-run correlation with run-log resolution and history filtering.
- [x] Phase/objection-aware UVM lifecycle trace normalization and SQLite persistence.
- [x] Explicit `sequencer@@sequence` report-context evidence where present.
- [x] Normalized sequence state lifecycle reconstruction through an explicit JSON contract.
- [x] Normalized sequence-item handshake analysis with run correlation, SQLite event/violation persistence, history filtering, and violation queries.
- [x] Observed grant order plus explicit ARB_REQUEST contention/bypass evidence and opt-in bypass bounds.
- [ ] Automatic sequence/item instrumentation adapters and vendor-policy-aware arbitration reconstruction.

## v0.7 — Formal

- [x] Formal adapter API.
- [ ] Bounded checks.
- [ ] Property/cover result persistence and tool-specific ingestion.
- [ ] Counterexample artifact model.
- [ ] Formal coverage.

## v0.8 — Intelligent Debug

- Log/waveform/assertion correlation.
- Failure clustering.
- Root-cause candidate ranking from evidence.
- Suggested next debug probes.
- Coverage-hole test suggestions.
- Generated assertions/tests kept reviewable and opt-in.

## Long-Term Direction

The long-term target is a unified digital design and verification environment rather than a simulator clone: one project model and verification database across simulation, regression, assertions, coverage, protocols, waveform debug, formal, and intelligent triage.

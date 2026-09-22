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
consistency checks are also implemented; exhaustive optional AXI4 sideband/coherency
semantics, structural CDC signoff, and specification-complete UCIe checking remain planned.

- [x] APB normalized-trace reconstruction and protocol checks.
- [x] AXI4-Lite five-channel transaction reconstruction and protocol checks.
- [x] AXI4 burst normalized-trace foundation (IDs, lengths/types, WLAST/RLAST, 4KB rule).
- [x] AXI4 burst VCD extraction with timestamped transaction reconstruction.
- [x] AXI4 exclusive-access size/alignment, response, timing, and observed-pair checking.
- [x] AXI4 AxCACHE/AxPROT/AxQOS/AxREGION width checks and AxREGION 4KB consistency.
- [ ] Exhaustive AXI4 optional-sideband/coherency-adjacent checking.
- [x] Asynchronous FIFO / CDC-oriented normalized-event invariant checks.
- [x] UCIe public 68B/256B FLIT trace and link-health foundation.
- [ ] Specification-complete UCIe protocol/PHY conformance checking.

Each pack should support reusable checks, assertions, transaction extraction, coverage goals, and debug summaries.

## v0.6 — UVM and Commercial Simulator Adapters

Current status: the simulator-independent UVM report-log ingestion foundation is implemented.
ZDDV extracts standard UVM report messages, test names, severity summaries, report IDs,
components, source locations, and timestamps; persists normalized snapshots/messages in SQLite;
and exposes `uvm-analyze` plus `uvm-history`. Commercial simulator execution adapters and
phase/objection/sequence lifecycle reconstruction remain planned.

- [ ] Questa execution adapter.
- [ ] VCS execution adapter.
- [ ] Xcelium execution adapter.
- [x] Simulator-independent UVM report/test metadata ingestion and SQLite persistence.
- [ ] Sequence/phase/objection-aware result ingestion where supported.

## v0.7 — Formal

- Formal adapter API.
- Bounded checks.
- Property/cover result normalization.
- Counterexample artifact model.
- Formal coverage.

## v0.8 — Intelligent Debug

- Log/waveform/assertion correlation.
- Failure clustering.
- Root-cause candidate ranking from evidence.
- Suggested next debug probes.
- Coverage-hole test suggestions.
- Generated assertions/tests kept reviewable and opt-in.

## Long-Term Direction

The long-term target is a unified digital design and verification environment rather than a simulator clone: one project model and verification database across simulation, regression, assertions, coverage, protocols, waveform debug, formal, and intelligent triage.

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

Current status: deterministic source indexing, source-level hierarchy,
simulator-resolved elaborated hierarchy, and the first protocol transaction
reconstruction path (normalized APB traces) are implemented. Waveform-driven
protocol extraction remains planned.

- [x] Design hierarchy (source-level + simulator-elaborated).
- [x] Source indexing.
- [ ] Waveform index.
- [ ] Drivers/loads navigation.
- [ ] Assertion-to-waveform correlation.
- [x] Protocol transaction reconstruction (APB normalized-trace foundation).

## v0.5 — Protocol Verification

Initial protocol packs:

- APB
- AXI4 / AXI4-Lite
- asynchronous FIFO / CDC-oriented checks
- UCIe-oriented transaction analysis

Each pack should support reusable checks, assertions, transaction extraction, coverage goals, and debug summaries.

## v0.6 — UVM and Commercial Simulator Adapters

- Questa adapter.
- VCS adapter.
- Xcelium adapter.
- UVM test metadata.
- Sequence/phase/objection-aware result ingestion where supported.

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

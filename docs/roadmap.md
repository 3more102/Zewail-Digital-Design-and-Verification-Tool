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

Current status: normalized Verilator coverage ingestion, SQLite coverage snapshots,
coverage history, coverage merge, coverage-hole reporting, assertion result storage, and failure clustering are implemented.

- [x] Assertions database.
- [x] Code-coverage ingestion.
- [ ] Functional-coverage schema.
- [x] Coverage merge.
- [x] Coverage-hole reporting.
- [x] Failure signatures and clustering.
- [x] Normalized coverage history/trend.

## v0.4 — Debug Studio Core

- Design hierarchy.
- Source indexing.
- Waveform index.
- Drivers/loads navigation.
- Assertion-to-waveform correlation.
- Protocol transaction reconstruction.

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

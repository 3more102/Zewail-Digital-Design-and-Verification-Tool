# Zewail Digital Design and Verification Tool (ZDDV)

ZDDV is an open digital design and verification environment for RTL development, simulation orchestration, regression, coverage, assertions, waveform debugging, protocol verification, and future formal/AI-assisted verification workflows.

> Status: early development (v0.1 foundation)

## v0.1 Goal

The first milestone is intentionally small and executable:

```text
Create project
    ↓
Add RTL + testbench
    ↓
Select simulator
    ↓
Build
    ↓
Run
    ↓
PASS / FAIL
    ↓
Inspect log + waveform
```

The first simulator backend is **Verilator**. The architecture is backend-oriented so adapters for Questa, VCS, Xcelium, and other simulators can be added later without changing the project model.

## Planned CLI

```bash
zddv init my_project
zddv add rtl rtl/*.sv
zddv add tb tb/*.sv
zddv config simulator verilator
zddv build
zddv run
zddv run --test smoke --seed 100
zddv regress regression.yaml
zddv coverage
zddv debug <run-id>
```

## Core Architecture

- Project model and configuration
- Simulator adapter interface
- Verilator backend
- Build/run orchestration
- Structured run database
- Regression manager
- Coverage collection
- Assertion result handling
- Waveform artifacts
- Protocol analyzers
- UVM inspection
- Formal adapters
- AI-assisted debug

## Repository Layout

```text
src/zddv/          Python package and CLI
examples/          Small RTL verification examples
tests/             Unit and integration tests
docs/              Architecture and roadmap
.github/workflows/ CI
```

## Design Principles

1. Simulator-independent orchestration.
2. Reproducible runs: command, seed, sources, simulator version, logs, and artifacts belong to the run record.
3. Open formats wherever practical.
4. CLI-first core; GUI consumes the same APIs.
5. Protocol-aware verification rather than a waveform viewer only.
6. Incremental development with executable examples and tests.

## Initial Development Roadmap

### Phase 1 — Executable Core
- [ ] Project initialization
- [ ] TOML project configuration
- [ ] Source discovery
- [ ] Verilator installation/version detection
- [ ] Compile/build command
- [ ] Simulation command
- [ ] PASS/FAIL classification
- [ ] Run directories and logs
- [ ] VCD/FST waveform handling
- [ ] Counter/FIFO example

### Phase 2 — Regression
- [ ] Test definitions
- [ ] Seed sweeps
- [ ] Parallel workers
- [ ] Timeouts
- [ ] JSON/SQLite results
- [ ] Failure grouping
- [ ] HTML/CLI reports

### Phase 3 — Verification Intelligence
- [ ] Assertions
- [ ] Functional/code coverage ingestion
- [ ] AXI/APB protocol analysis
- [ ] Source/hierarchy database
- [ ] Waveform cross-probing
- [ ] UVM-aware result model

### Phase 4 — Advanced Verification
- [ ] Formal tool adapters
- [ ] Coverage-hole analysis
- [ ] Automated failure triage
- [ ] AI-assisted root-cause analysis

## License

A license will be selected before the first public release.

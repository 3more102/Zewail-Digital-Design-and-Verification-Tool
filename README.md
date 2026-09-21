# Zewail Digital Design and Verification Tool (ZDDV)

[![ZDDV CI](https://github.com/3more102/Zewail-Digital-Design-and-Verification-Tool/actions/workflows/ci.yml/badge.svg)](https://github.com/3more102/Zewail-Digital-Design-and-Verification-Tool/actions/workflows/ci.yml)

ZDDV is an open digital design and verification environment for RTL development, simulation orchestration, regression, coverage, waveform artifacts, and future assertion, protocol, formal, UVM, and AI-assisted verification workflows.

> Status: **v0.4 Debug Studio Core — source/hierarchy indexing in progress**

## What Works Today

- TOML-based verification projects
- RTL/testbench source discovery
- Simulator-adapter architecture
- Verilator detection and version reporting
- SystemVerilog compile/elaboration
- Self-checking simulation with PASS / FAIL / TIMEOUT results
- Named tests, deterministic seeds, runtime plusargs, and per-test timeouts
- Parallel seeded regressions
- Isolated run directories
- Build and run JSON manifests
- Simulation logs
- VCD/FST artifact discovery
- Verilator code-coverage collection
- Coverage merge/report flow
- Normalized Verilator coverage metrics stored as SQLite snapshots
- Coverage history/trend CLI with per-type point breakdown
- Coverage-hole analysis with type filtering and JSON export
- Normalized assertion result database keyed by simulation run
- Simulator-independent functional coverage snapshots and per-bin database
- Compatibility path for packaged Verilator 5.020 coverage generation
- SQLite verification results database and run history
- Selective rerun of historical PASS / FAIL / TIMEOUT runs
- JUnit XML export for CI systems
- Failure-signature normalization and grouping across failing seeds
- CI on Python 3.11, 3.12, and 3.13
- Static SystemVerilog module/source index and top-rooted instance hierarchy
- VCD waveform scope/signal indexing linked to verification runs
- End-to-end Verilator CI example

## Quick Start

Requirements:

- Python 3.11+
- Verilator available in `PATH`

Install ZDDV for development:

```bash
git clone https://github.com/3more102/Zewail-Digital-Design-and-Verification-Tool.git
cd Zewail-Digital-Design-and-Verification-Tool
python -m pip install -e ".[dev]"
zddv doctor
```

Run the included counter example:

```bash
zddv --project examples/counter build
zddv --project examples/counter run --test counter_basic --seed 42
zddv --project examples/counter regress examples/counter/regression.toml
zddv --project examples/counter coverage
```

## Current CLI

```bash
zddv init my_project

zddv --project my_project add rtl "rtl/*.sv"
zddv --project my_project add tb "tb/*.sv"

zddv --project my_project config simulator verilator
zddv --project my_project config top tb_top

zddv doctor
zddv --project my_project lint
zddv --project my_project build

zddv --project my_project run
zddv --project my_project run --test smoke --seed 100
zddv --project my_project run --plusarg +MODE=1 --timeout 30

zddv --project my_project regress regression.toml
zddv --project my_project runs --limit 20
zddv --project my_project rerun --status FAIL --status TIMEOUT --limit 20
zddv --project my_project junit --output .zddv/junit.xml --limit 100
zddv --project my_project failures --limit 200
zddv --project my_project report --limit 100
zddv --project my_project index
zddv --project my_project hierarchy
zddv --project my_project wave-index
zddv --project my_project wave-index --run <run-id>
zddv --project my_project coverage
zddv --project my_project coverage-history --limit 20
zddv --project my_project coverage-holes --show 20
zddv --project my_project coverage-holes --type line --output .zddv/coverage/line-holes.json
zddv --project my_project assertions --limit 100
zddv --project my_project assertions --status FAIL
zddv --project my_project fcov-import functional_coverage.json
zddv --project my_project fcov-history --limit 20
zddv --project my_project fcov-holes --limit 50
```

## Verification Flow

```text
zddv.toml
    │
    ├── RTL / SystemVerilog testbench
    │
    ▼
Project + Source Model
    │
    ▼
Simulator Adapter
    │
    └── Verilator
           │
           ├── build.log
           ├── build.json
           └── zddv_sim
                  │
                  ▼
              Run Engine
                  │
          ┌───────┼────────┐
          │       │        │
         log   waveform  coverage.dat
          │       │        │
          └───────┼────────┘
                  ▼
               run.json
                  │
                  ▼
          Regression Engine
                  │
                  ├── seed sweeps
                  ├── parallel workers
                  ├── timeout handling
                  └── regression JSON
                  │
                  ▼
             Coverage Merge
```

## Project Configuration

Example `zddv.toml`:

```toml
[project]
name = "counter"

[simulator]
backend = "verilator"
top = "tb_counter"

[sources]
rtl = ["rtl/*.sv"]
tb = ["tb/*.sv"]

[run]
build_dir = ".zddv/build"
run_dir = ".zddv/runs"
waveform = true
coverage = true
```

## Regression Definition

Example `regression.toml`:

```toml
[regression]
name = "counter-smoke"
jobs = 2

[[tests]]
name = "counter_basic"
seeds = [1, 7, 42, 100]
timeout_s = 10
plusargs = []
```

Each test/seed executes in an isolated run directory and produces reproducible metadata including simulator version, command line, seed, status, logs, waveform path, and coverage artifact.

## Core Architecture

```text
CLI / future GUI
       │
       ▼
    ZDDV Core
       │
       ├── Project Model
       ├── Run/Result Model
       ├── Regression Engine
       └── Coverage Engine
       │
       ▼
 Simulator Adapter API
       │
       ├── Verilator  ← implemented
       ├── Questa     ← planned
       ├── VCS        ← planned
       └── Xcelium    ← planned
```

The CLI and future GUI must use the same core APIs. Simulator-specific command construction stays inside simulator adapters.

## Repository Layout

```text
src/zddv/              Python package and CLI
src/zddv/simulator/    Simulator adapter layer
examples/counter/      Self-checking SystemVerilog example
tests/                 Unit and integration tests
docs/                  Architecture and roadmap
.github/workflows/     Continuous integration
```

## Development Status

### Phase 1 — Executable Core

- [x] Project initialization
- [x] TOML project configuration
- [x] Source discovery
- [x] Verilator installation/version detection
- [x] Compile/build command
- [x] Simulation command
- [x] PASS / FAIL / TIMEOUT classification
- [x] Isolated run directories and logs
- [x] JSON build/run metadata
- [x] Waveform artifact handling
- [x] Self-checking counter example
- [ ] FIFO example

### Phase 2 — Regression

- [x] Test definitions
- [x] Seed sweeps
- [x] Parallel workers
- [x] Per-test timeouts
- [x] JSON regression results
- [x] CLI regression summary
- [x] SQLite result database
- [x] Selective rerun
- [x] JUnit XML export
- [x] Failure signature grouping
- [x] HTML report

### Phase 3 — Coverage and Verification Intelligence

ZDDV defines the normalized **hit rate** as the percentage of parsed Verilator
coverage points whose runtime counter is greater than zero. This is stored
separately from Verilator's annotation threshold.

- [x] Verilator coverage instrumentation
- [x] Per-run coverage artifacts
- [x] Multi-run coverage merge
- [x] Normalized coverage metrics/database
- [x] Assertion result database
- [x] Functional coverage schema and JSON ingestion
- [x] Coverage-hole analysis
- [ ] APB protocol analysis
- [ ] AXI4 / AXI4-Lite protocol analysis
- [ ] UCIe transaction analysis
- [x] Static source/module index and design hierarchy
- [x] VCD waveform scope/signal index
- [ ] Waveform cross-probing
- [ ] UVM-aware result model

### Assertion Result Markers

ZDDV can ingest simulator-independent assertion results from testbench logs using a
small normalized marker format:

```text
ZDDV_ASSERT fifo_no_overflow PASS depth=4
ZDDV_ASSERT axi_response_valid FAIL unexpected_BRESP
```

Each event is stored with its run ID, assertion name, status, message, log path,
and source log line. Simulator adapters can translate native assertion output into
this same database model over time.

### Functional Coverage Input

ZDDV v0.3 defines a simulator-independent JSON model for functional coverage.
Each bin records its scope, coverpoint, bin name, observed hits, goal, and optional
metadata. Coverage status is derived from `hits >= goal` and persisted in SQLite.

```json
{
  "source": "uvm-export",
  "bins": [
    {"scope": "tb.axi", "coverpoint": "burst_len", "bin": "len1", "hits": 8, "goal": 1},
    {"scope": "tb.axi", "coverpoint": "burst_len", "bin": "len16", "hits": 0, "goal": 1}
  ]
}
```

This normalized model is intentionally simulator-independent so later Questa,
VCS, Xcelium, or UVM exporters can feed the same verification database.

### Phase 4 — Advanced Verification

- [ ] Questa adapter
- [ ] VCS adapter
- [ ] Xcelium adapter
- [ ] Formal adapter API
- [ ] Counterexample normalization
- [ ] Automated failure triage
- [ ] AI-assisted root-cause analysis
- [ ] Desktop debug GUI

## Design Principles

1. Simulator-independent orchestration.
2. Reproducible runs: simulator version, command, seed, sources, logs, and artifacts are part of the run record.
3. Open formats wherever practical.
4. CLI-first core; the future GUI consumes the same APIs.
5. Protocol-aware verification rather than only waveform viewing.
6. Compatibility with practical tool versions, not only the newest simulator release.
7. Every milestone must have an executable example and CI test.

## Documentation

- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)

## License

A license will be selected before the first public release.

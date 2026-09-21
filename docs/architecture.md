# ZDDV Architecture

## Objective

ZDDV is designed as a simulator-independent verification orchestration and debug platform. The core owns project metadata, run reproducibility, result models, regressions, coverage, and debug intelligence. Simulator-specific behavior is isolated behind adapters.

## Layered Model

```text
CLI / future GUI
       |
   ZDDV Core
       |
 Project Model ---- Run/Result Database
       |
 Simulator Adapter API
       |
 +-----+---------+---------+---------+
 |               |         |         |
Verilator      Questa     VCS     Xcelium
```

Future verification services consume the same run/result model:

```text
simulation -> assertions -> coverage -> protocol analysis
           -> waveform/debug -> failure triage -> AI analysis
```

## v0.1 Contracts

### Project

A project is rooted by `zddv.toml`. It defines:

- project name
- simulator backend
- top module
- RTL patterns
- testbench patterns
- build directory
- run directory
- waveform policy

### Build

A build produces:

- simulator command
- combined build log
- executable, when successful
- `build.json` manifest

### Run

Every run receives a unique run ID and directory. A run produces:

- simulation log
- PASS/FAIL status
- return code
- detected waveform artifact
- `run.json` metadata

This run directory becomes the atomic unit for future regression, coverage, debug, and failure-clustering features.

## Simulator Adapter Rule

No CLI or GUI feature should contain simulator-specific command construction. All simulator-specific compile/run logic belongs in `src/zddv/simulator/`.

## Current Regression Data Plane

ZDDV now preserves each simulation in both its per-run JSON artifact and the
project SQLite database. Historical FAIL/TIMEOUT runs can be selectively rerun
while preserving the original test name, seed, plusargs, and timeout. Run history
can also be exported as JUnit XML or JSON for CI and external reporting.

## Next Architectural Steps

1. Define normalized assertion and coverage schemas.
2. Add failure signatures and clustering.
3. Add waveform indexing and source/hierarchy metadata.
4. Add protocol-aware analyzers.
5. Add formal adapters.
6. Add AI-assisted triage over normalized ZDDV evidence.

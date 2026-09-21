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

## Next Architectural Steps

1. Add test/seed/plusarg models.
2. Add regression scheduler and worker pool.
3. Move run records into SQLite while preserving JSON artifacts.
4. Define normalized assertion and coverage schemas.
5. Add waveform indexing and source/hierarchy metadata.
6. Add protocol-aware analyzers.
7. Add formal adapters.
8. Add AI-assisted triage over normalized ZDDV evidence.


## v0.4 Static Design Index

The first Debug Studio service is a simulator-independent static design index. ZDDV scans the configured SystemVerilog sources, records module definitions and source locations, resolves instances whose module types exist in the same project, and builds a hierarchy rooted at the configured top module.

The normalized artifact is written to:

```text
.zddv/index/design.json
```

This artifact is intentionally separate from simulator-generated elaboration data. It gives the CLI and future GUI a stable source/hierarchy model now, while later adapters can enrich it with elaborated parameters, generated scopes, signal metadata, and simulator-specific handles.

The next Debug Studio layer will add a waveform index and correlate waveform scopes back to these hierarchy paths.


## v0.4 Waveform Index

ZDDV can index VCD headers independently of the simulator that produced them. The waveform index records scope paths, signal paths, VCD identifier codes, widths, variable types, and timescale without loading the full value-change stream.

Waveform indexes are linked to ZDDV run IDs when available and are written under:

```text
.zddv/index/waveforms/<run-id>.json
```

Keeping header indexing separate from value decoding makes the first debug operations fast and gives later cross-probing code a compact signal catalog before selective waveform data is read.

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

## v0.4 Design Index Contract

The Debug Studio core uses a normalized design index at
`.zddv/design/index.json`. The source-level index contains:

- source file paths, line counts, byte counts, and SHA-256 hashes
- module, interface, program, and package definitions with source ranges
- known design-unit instantiations with source lines
- duplicate design-unit detection
- a recursive hierarchy rooted at the configured project top

The source-level index is deliberately separate from simulator elaboration.
Later adapters may enrich this model with elaborated hierarchy and tool-specific
AST evidence without changing the CLI/GUI-facing contract.

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

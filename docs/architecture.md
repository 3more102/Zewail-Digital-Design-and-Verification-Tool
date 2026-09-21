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
Simulator-resolved evidence is written to `.zddv/design/elaborated.json`.
The Verilator adapter exports JSON AST data on Verilator 5.022+ and falls back
to legacy XML on older supported releases. ZDDV normalizes both forms into
module records, source locations, and full instance paths while preserving the
source-index contract for CLI/GUI consumers.

## v0.4 Waveform Index Contract

`zddv waveform-index` normalizes waveform metadata under
`.zddv/waveforms/`. For VCD artifacts the index contains the declaration
timescale, hierarchical scopes, signal paths, widths, VCD identifier codes, and
artifact fingerprints. The parser stops at `$enddefinitions` and does not load
value-change samples while building the signal catalog.

FST artifacts are fingerprinted and recorded as metadata-only until an FST
converter or simulator-native waveform adapter is available. This distinction is
explicit in the `parse_status` field so downstream debug features do not treat
metadata-only artifacts as fully indexed waveforms.

## v0.4 Assertion / Waveform Correlation

`zddv assertion-waveform` joins normalized assertion events to their exact
simulation run and recorded waveform. VCD-backed runs reuse the waveform index,
while FST-backed runs remain explicitly metadata-only until an adapter is
available.

Correlation is evidence-based: signal hints are emitted only when identifiers in
the assertion name/message match a waveform signal by exact hierarchical path or
exact signal name (including underscore-separated message keys such as
`final_count` -> `count`). No fuzzy root-cause claim is made.

## v0.4 Connectivity Index Contract

`zddv connectivity` writes `.zddv/design/connectivity.json` as a normalized
source-level structural connectivity model. Each evidence edge records a design
unit, signal, driver/load role, evidence kind, source file, and line. Known child
instance connections also retain the instance name, child type, port name,
direction, and connected expression.

The initial analyzer recognizes simple continuous/procedural assignments, port
boundaries, and named or positional connections to known child design units.
Input ports are modeled as boundary drivers into a unit, output ports as boundary
loads leaving a unit, and inouts as both. At a child instance, an input consumes
the parent signal (load) and an output drives the parent signal (driver).

The contract is explicitly tagged `analysis_level = "source_structural"`.
Generate-time elaboration, preprocessor-dependent structure, binds,
interface/modport semantics, complex lvalues, and other unresolved constructs
must be enriched by simulator AST/elaboration adapters rather than guessed.

## v0.4 Waveform / Source Cross-Probe Contract

`zddv crossprobe <signal>` composes the normalized waveform index with the
source-level design hierarchy. Signal resolution is deterministic: exact path,
unique hierarchy suffix, then unique short name. Ambiguous short-name matches are
rejected instead of guessed.

A successful source match records the waveform signal, matched hierarchy scope,
design instance path and type, source design unit, and exact declaration line
when one can be identified conservatively. Results remain `PARTIAL` when the
waveform scope maps to a design unit but an exact declaration cannot be proven.

The source cross-probe remains conservative, while simulator-resolved hierarchy is available separately through the elaboration index. Generated signal/connectivity semantics still require richer simulator AST evidence before ZDDV should claim exact elaborated drivers/loads.

## Simulator Adapter Rule

No CLI or GUI feature should contain simulator-specific command construction. All simulator-specific compile/run logic belongs in `src/zddv/simulator/`.

## Next Architectural Steps

1. Add test/seed/plusarg models.
2. Add regression scheduler and worker pool.
3. Move run records into SQLite while preserving JSON artifacts.
4. Define normalized assertion and coverage schemas.
5. Enrich source/connectivity/hierarchy/waveform cross-probing with simulator elaboration and generated hierarchy.
6. Add protocol-aware analyzers.
7. Add formal adapters.
8. Add AI-assisted triage over normalized ZDDV evidence.

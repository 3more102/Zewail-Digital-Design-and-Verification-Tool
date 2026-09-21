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

`zddv crossprobe <signal>` composes normalized evidence across the waveform,
source hierarchy, and source-level connectivity models. Signal resolution is
deterministic: exact path, unique hierarchy suffix, then unique short name.
Ambiguous short-name matches are rejected instead of guessed.

A successful source match records the waveform signal, matched hierarchy scope,
design instance path and type, source design unit, exact declaration line when
one can be identified conservatively, and the structural driver/load evidence
available for the same source signal. The report also records the design,
connectivity, and waveform index paths used as evidence. Results remain
`PARTIAL` when the waveform scope maps to a design unit but an exact declaration
cannot be proven.

This contract is source-level. Generated hierarchy, parameter-specialized
instances, binds, macros, and simulator-resolved objects remain enrichment work
for simulator AST/elaboration adapters.

## v0.5 AXI4-Lite Protocol Analysis Contract

`zddv axi4lite-analyze` consumes a simulator-independent JSON trace whose
samples represent values observed on ACLK edges. The analyzer keeps independent
state for the AW, W, B, AR, and R channels and treats a transfer as accepted only
when the channel's VALID and READY signals are both asserted in the same sample.

Write address and write data are accepted independently and paired in acceptance
order. Read and write responses are correlated in order because AXI4-Lite has no
transaction IDs. The analyzer permits multiple outstanding transactions while
preserving that ordering constraint.

The normalized checks cover:

- VALID remaining asserted until its READY handshake completes.
- Channel payload remaining stable while VALID is asserted and READY is LOW.
- Required payload presence for active channels.
- Responses appearing only after their corresponding requests.
- Incomplete requests/responses at trace end.
- AXI4-Lite response legality, including rejection of EXOKAY.
- Legal SLVERR/DECERR responses as transaction outcomes rather than protocol failures.

This contract is AXI4-Lite only. It does not claim support for full AXI4 bursts,
IDs, reordering, burst types, beat counting, WLAST/RLAST semantics, or
out-of-order response matching. Those belong to a later full-AXI protocol pack.

## v0.5 AXI4 Burst Trace Foundation

`zddv axi4-analyze` consumes normalized ACLK-edge samples for burst-aware AXI4.
It tracks AW/AR IDs and burst geometry, pairs W data bursts to write addresses in
acceptance order because AXI4 has no WID, correlates BID/RID responses by ID, and
allows read data from different IDs to interleave.

The foundation checks VALID/payload stability under backpressure, WLAST/RLAST
against AxLEN, FIXED/INCR/WRAP burst geometry, WRAP length/alignment, and the 4KB
burst-boundary rule. Legal SLVERR/DECERR responses remain transaction outcomes
rather than protocol violations.

ACE coherency, AXI5 additions, USER sidebands, QoS policy, and exhaustive
exclusive/system-ordering semantics remain outside this first AXI4 contract.

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

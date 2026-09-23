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
- executable or simulator-native compiled-library artifact, when successful
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
to legacy XML on older supported releases. Verilator 5.022 introduced
`--json-only`; Verilator 5.044 was the last release supporting the deprecated
XML-only path. ZDDV normalizes both forms into
module records, source locations, and full instance paths while preserving the
source-index contract for CLI/GUI consumers.

## v0.4 Waveform Index Contract

`zddv waveform-index` normalizes waveform metadata under
`.zddv/waveforms/`. For VCD artifacts the index contains the declaration
timescale, hierarchical scopes, signal paths, widths, VCD identifier codes, and
artifact fingerprints. The parser stops at `$enddefinitions` and does not load
value-change samples while building the signal catalog.

FST artifacts are fingerprinted and recorded as metadata-only by default. An
explicit `fst2vcd` adapter can be enabled by the caller to convert one FST into a
temporary VCD, reuse the VCD declaration parser, and return
`parse_status = "indexed-via-fst2vcd"` with converter provenance. The temporary
VCD is removed after use, converter execution never uses a shell, and converter
failures or security rejections are surfaced rather than bypassed. Downstream
features must still opt in to this adapter explicitly; metadata-only remains the
fail-closed default.

## v0.4 Assertion / Waveform Correlation

`zddv assertion-waveform` joins normalized assertion events to their exact
simulation run and recorded waveform. VCD-backed runs reuse the waveform index.
Assertion correlation never invokes external conversion implicitly. FST-backed runs
remain metadata-only by default; callers may opt in explicitly with the same
`fst2vcd` adapter. An opted-in run is indexed once per correlation pass, reports
`parse_status = "indexed-via-fst2vcd"`, preserves converter provenance, and keeps
the original FST as the waveform artifact of record.

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
Ambiguous short-name matches are rejected instead of guessed. FST inputs remain
metadata-only unless the caller explicitly supplies the `fst2vcd` adapter; opted-in
cross-probing accepts `indexed-via-fst2vcd` evidence and records the adapter
provenance without retaining the temporary VCD.

A successful source match records the waveform signal, matched hierarchy scope,
design instance path and type, source design unit, exact declaration line when
one can be identified conservatively, and the structural driver/load evidence
available for the same source signal. The report also records the design,
connectivity, and waveform index paths used as evidence. Results remain
`PARTIAL` when the waveform scope maps to a design unit but an exact declaration
cannot be proven.

Cross-probing consumes persisted simulator-elaborated hierarchy when
`.zddv/design/elaborated.json` is present and its project, top, simulator, and
design-revision fingerprint match the active project. The fingerprint covers the
configured RTL/testbench source patterns and SHA-256 content of the resolved
sources, so hierarchy captured before an RTL/config revision is rejected as stale.
Scope resolution prefers valid simulator-resolved evidence, including
generated-scope paths, then falls back to the deterministic source hierarchy.
Invalid or stale persisted elaboration is reported as evidence state and is never
guessed or regenerated implicitly.

Source declaration and driver/load evidence remain tied to the source design
index and `analysis_level = "source_structural"`. When a waveform scope is
resolved by valid simulator-elaborated hierarchy, cross-probing qualifies those
same source-structural edges with the exact parent instance path. Instance-port
edges also retain an exact elaborated child path when one candidate is proven;
multiple generated candidates are reported as ambiguous rather than selecting one.
Direct normalized CELL PIN-to-VARREF records add a separate
`elaborated_connectivity` view. Normalized bindings can relate an exact parent
signal to a child pin and attach module-port direction only when the persisted
port-direction evidence is itself normalized. A child-pin query whose persisted
binding is `UNSUPPORTED` is retained separately as
`unsupported_instance_port_bindings`, including the expression type and pin
location but no inferred parent signal or relationship. Malformed normalized
pin-binding containers are rejected before cross-probing.

For normalized direct bindings, cross-probing can also correlate the exact
child path/pin/parent-signal tuple with the already-qualified source
`instance_port` edge. A unique structural match is retained as cross-evidence
correlation. When source qualification is ambiguous only because one static
instance declaration expands into multiple explicit generated child candidates,
the direct pin binding may select its exact child path only when that path is
already present in `elaborated_child_candidates`; the retained source edge still
records the original ambiguous candidate set. Other missing or multiple source
edges stay explicit. Driver/load roles attached to this correlation are labeled
`source_structural_only` and are not promoted to simulator-elaborated semantics.

Separately, when a direct normalized binding also has trusted module-port direction,
`elaborated_connectivity` classifies that module-boundary relation relative to the
queried waveform signal as a `boundary_driver`, `boundary_load`, or both for
`inout`. Missing or ambiguous direction is retained in
`boundary_unclassified_bindings`, and unsupported expressions are excluded from
these role lists.

A separate `elaborated_internal_connectivity` view consumes only normalized
module-root Verilator `ASSIGNW` records whose left and right expressions are each a
plain `VARREF`. For such exact records, the right-hand signal is a direct continuous
assignment driver of the left-hand signal; querying the left side yields a driver edge,
while querying the right side yields a load edge. The contract is tagged
`simulator_elaborated_module_root_assignw_direct_varref` and remains separate from
source-structural and module-boundary role evidence.

Any `ASSIGNW` whose left or right side is not a plain `VARREF` is retained only as
`UNSUPPORTED` reference evidence. If the queried signal occurs inside that expression,
the internal connectivity result becomes `PARTIAL` and records the unresolved assignment
without creating a driver or load edge. Legacy XML, generated-scope assignments,
procedural assignments, selects, concatenations, and other complex expressions remain
outside this contract until their Verilator JSON schemas are explicitly normalized.

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

Observed optional AXI4 request sidebands are validated at the interface boundary.
ZDDV width-checks AxCACHE (4 bits), AxPROT (3 bits), AxQOS (4 bits), and AxREGION
(4 bits), preserves them in reconstructed transactions, and exposes both generic
metadata keys and channel-specific AR/AW aliases. AxCACHE decoding follows the AXI4
directional definitions: ARCACHE[2]/AWCACHE[3] are the current transaction's
Allocate bit, while ARCACHE[3]/AWCACHE[2] are Other Allocate. The analyzer reports
these semantics, their source bit positions, cache-lookup requirement, and the
observable memory-class family (Device, Normal Non-cacheable, Write-Through, or
Write-Back). Legacy read-allocate/write-allocate raw-bit fields remain in the report
for schema compatibility. It does not infer downstream cache topology or whether an
allocation actually occurred. When AxREGION is present, its observed value must
remain constant for requests in the same 4KB address space.

This decoding follows Arm AMBA AXI and ACE Protocol Specification IHI 0022H,
Tables A4-3, A4-4, and A4-5.

Optional AWUSER, WUSER, BUSER, ARUSER, and RUSER values are transported as opaque
channel payload. When present, they participate in the same VALID/READY stability
checks as the rest of their channel and are preserved in reconstructed transaction
evidence. Their meaning remains implementation-defined, so ZDDV does not invent
semantic legality rules for their bit values. Explicit `user_signal_widths`
metadata validates raw physical absence for zero-width signals and requires every
configured non-zero USER signal on each sample where its corresponding channel
VALID is asserted, including stalled cycles. Values on those active cycles must fit
the configured unsigned width. When enough widths are supplied, ZDDV also enforces
the Arm IHI 0022 configuration relationships: AWUSER and ARUSER share
`USER_REQ_WIDTH`, while RUSER is `USER_DATA_WIDTH + USER_RESP_WIDTH` (the WUSER
and BUSER widths). Missing metadata members stay unknown rather than being inferred.

The analyzer keeps Arm USER guidance separate from protocol legality. It records
non-failing advisories for the guidance maxima on `USER_REQ_WIDTH`,
`USER_DATA_WIDTH`, and `USER_RESP_WIDTH`, for the recommended USER data-width
granularity, and for changing lower RUSER response bits across a multi-beat read
response when the required width evidence is explicit. Advisory evidence is
reported independently of `status` and never upgrades a recommendation into a
mandatory AXI4 rule.

A normalized trace can additionally provide `address_width_bits` for the AXI
`ADDR_WIDTH` interface property. The metadata must be 1..64 bits; accepted
AWADDR/ARADDR values that do not fit that width are reported as protocol-evidence
violations. Missing address-width metadata remains unknown rather than being
inferred from observed transaction values.

A normalized trace can additionally provide `data_width_bits` using a standard
AXI data width of 8/16/32/64/128/256/512/1024 bits. When present, ZDDV rejects
ARSIZE/AWSIZE transfers wider than that interface width. For accepted write beats,
WSTRB must fit the physical strobe width and every asserted bit must belong to the
byte lanes permitted by that beat's address and transfer size. Narrow, unaligned,
FIXED, INCR, and WRAP addressing therefore receive beat-specific legal masks.
Deasserting any valid lane, including an all-zero WSTRB, remains legal.

These checks intentionally stop at properties observable from the normalized
interface trace. ACE coherency, AXI5 additions, system-level QoS policy,
topology-dependent cache reachability, and exhaustive system-ordering semantics
remain outside this AXI4 contract.

## v0.5 AXI4 Waveform Extraction Contract

`zddv axi4-waveform` converts a VCD-backed AXI4 interface into the same normalized
ACLK-edge trace consumed by `zddv axi4-analyze`. Scope selection is deterministic:
an explicit scope must contain the required five-channel handshake and burst payload
signals; automatic selection succeeds only when exactly one complete AXI4 scope exists.

The extractor streams selected VCD identifiers only, preserves optional transaction IDs,
address sidebands, and USER sidebands when present, requires AWADDR and ARADDR to
share one 1..64-bit `ADDR_WIDTH`, requires WDATA and RDATA to use the same standard
AXI data width, verifies that WSTRB has one bit per data byte,
derives `data_width_bits`, and records the declared VCD widths for USER signals
that are actually present in the selected scope. Those observed widths feed the
same core USER-width consistency checks; an undumped USER signal is not interpreted
as proof of a zero-width physical signal. Physical waveform timestamps are recorded
alongside logical sample cycles. The burst analyzer then propagates those timestamps
into violations and reconstructed AW/W/B/AR/R transaction evidence. VCD parsing remains
an input adapter; protocol semantics remain simulator-independent in the AXI4 core.

The data-width and WSTRB rules follow Arm AMBA AXI and ACE Protocol Specification
ARM IHI 0022H, including the data-bus/transfer-size constraint and section A3.4.4
write-strobe rules:
https://developer.arm.com/-/media/Arm%20Developer%20Community/PDF/IHI0022H_amba_axi_protocol_spec.pdf

## v0.5 UCIe Public FLIT Trace Foundation

`zddv ucie-analyze <trace.json>` consumes a simulator-independent normalized
FLIT trace. This first contract intentionally uses only facts documented in public
UCIe Consortium material: public UCIe material describes link initialization as
negotiating operating parameters including width, lane numbering, frequency, and
protocol support, while the Consortium's introductory webinar Q&A describes FLITs
as 68 or 256 bytes with ACK/NAK carried in a 2-byte header and CRC information in
the FLIT.

The normalized public profile therefore records:

- trace-order cycle and optional timestamp;
- TX/RX direction;
- 68-byte or 256-byte FLIT record size;
- the public 2-byte header model;
- monitor-normalized ACK/NAK indication;
- explicit monitor CRC health;
- optional negotiated width, lane numbering, frequency, and protocol metadata.

Trace-contract violations are kept separate from link-health observations. A malformed
direction, unsupported public-profile size, non-monotonic ordering, header mismatch, or
missing normalized ACK/NAK/CRC field makes the trace FAIL. A NAK or CRC failure marks
link health as DEGRADED but is not independently called a UCIe protocol violation,
because public sources do not provide enough information to infer retry correctness.

This is not a UCIe conformance checker. It does not encode evaluation-copy-only rules
for PHY electrical behavior, training-state timing, retry sequencing, protocol mappings,
lane repair, or exact CRC construction.

Public references:

- https://www.uciexpress.org/specifications
- https://www.uciexpress.org/post/introduction-to-ucie-webinar-q-a-recap

## v0.7 Formal Adapter Contract

Formal execution is separated from simulation by `src/zddv/formal/`. The base API defines
a simulator-independent request (`FormalCheckRequest`), per-property normalized evidence
(`FormalPropertyResult`), an overall tool result (`FormalCheckResult`), and the
`FormalBackend` execution boundary.

The contract deliberately does not infer vendor-specific proof semantics. It normalizes
only stable concepts: BMC/prove/cover request modes; optional bounds, property filters and
timeouts; assertion PASS/FAIL/UNKNOWN/ERROR states; cover
COVERED/UNCOVERED/UNKNOWN/ERROR states; tool command/evidence paths; and overall
PASS/FAIL/UNKNOWN/ERROR status. Normalized property/cover evidence is persisted separately
from execution.

The first concrete execution backend is SymbiYosys finite-depth BMC. ZDDV generates an
explicit `.sby` job with `mode bmc`, a required `depth`, optional SBY timeout,
the `smtbmc` engine, project source staging, and `prep -top` for the configured top.
A completed result is accepted only from an explicit SBY `DONE (..., rc=...)` terminal
marker; an external timeout is normalized as UNKNOWN and missing terminal evidence as
ERROR. A BMC PASS is bounded evidence only and is never promoted to an unbounded proof.

## Simulator Adapter Rule

No CLI or GUI feature should contain simulator-specific command construction. All simulator-specific compile/run logic belongs in `src/zddv/simulator/`.

Current executable backends:
- **Verilator**: executable binary flow with optional waveform/code-coverage artifacts.
- **Questa/QuestaSim foundation**: `vlib` + `vlog` compile into a simulator library, then `vsim -c` execution with deterministic seed/test/plusarg transport, timeout classification, optional VCD capture, assertion-log ingestion, and run-linked UVM log ingestion. When project coverage is enabled, the adapter instruments compilation with `+cover`, runs with `-coverage`, keeps the simulator alive after `$finish` with `-onfinish stop`, and saves a per-run `coverage.ucdb`. The coverage engine merges those UCDBs with `vcover merge`, parses the numeric `vcover report -summary` table, stores aggregate per-type bins/hits in the existing coverage snapshot schema, and preserves Questa's weighted total coverage separately. It also parses ordinary covergroup bins from `vcover report -cvg -details` into the simulator-independent functional-coverage schema; illegal/ignore bins are not rewritten as ordinary goals. For code coverage, ZDDV retains machine-readable XML detail output together with a complementary `vcover report -zeros -details -codeAll` source-detail artifact. Both are retained without guessing undocumented UCDB/XML internals. Separately, documented `vcover report -details -dumptables -code sbc` text rows are normalized into source-linked statement, branch, and condition FEC items for the shared coverage-hole flow. Expression/toggle/FSM item-level normalization remains a separate milestone.

- **Synopsys VCS foundation**: native `vcs` compilation generates `simv`; runtime orchestration carries deterministic `+ntb_random_seed`, ZDDV/UVM test selection, arbitrary plusargs, timeout classification, VCD capture, assertion ingestion, and run-linked UVM ingestion. When coverage is enabled, compile and run use `-cm line+cond+fsm+tgl+branch`; each run supplies its own `-cm_dir` and ZDDV records the resulting `coverage.vdb` only when the database exists. The coverage layer merges those per-run databases with URG into a combined `coverage.vdb` and retains the generated report directory. It parses the documented `dashboard.txt` Total Coverage Summary into the overall SCORE and available LINE/COND/TOGGLE/FSM/BRANCH/ASSERT/GROUP percentages. When the documented Total Groups Coverage Summary is present, ZDDV also records its global covergroup type and instance COVERED/EXPECTED counts in a separate count table linked to the score snapshot. ZDDV does not derive code-metric counts from percentages. It separately parses documented module-level Line and Branch total/covered rows from `modinfo.txt`, persists them as explicitly scoped `module_line` and `module_branch` counts, and retains per-module evidence in the coverage manifest. Separately, retained URG HTML detail pages are normalized from explicit instance-level Line, Cond, Total Bits toggle, and Branch total/covered rows. FSM state, transition, and sequence rows remain separate metrics rather than being collapsed into a synthetic FSM total; repeated/paginated instance evidence is deduplicated by instance/FSM identity. Condition/toggle/FSM module-level counts remain a separate milestone. Missing or unparseable dashboards remain evidence-only.

- **Cadence Xcelium foundation**: `xrun -elaborate` builds a reusable simulator database under the project build directory and `xrun -R -xmlibdirname <db>` executes that snapshot from isolated run directories. The adapter transports deterministic SystemVerilog seeds with `-svseed`, preserves ZDDV/UVM test plusargs, enforces external timeouts, ingests assertion markers and UVM logs into the shared run database, and can generate VCD evidence with an `-input` Tcl script using Xcelium `database`/`probe` commands. When coverage is enabled, elaboration uses `-coverage all`; each simulation selects an isolated Cadence workdir/scope/test hierarchy and ZDDV records the native run directory only when a `.ucd` file is present. The coverage engine writes those `.ucd` paths to an IMC runfile, merges them with `-metrics all -initial_model union_all`, validates the native merged `.ucm` and `.ucd` artifacts under `cov_work/scope/merged`, retains raw merge diagnostics, and loads that merged run before generating a cumulative all-metrics summary. The summary adapter preserves `Overall`, `Code`, `FSM`, and `Functional` `Average` and `Covered` grades separately, uses `Overall Covered` as the percentage-native history score, and stores only explicitly reported two-field covered/total counts. Unknown summary layouts remain evidence-only rather than being guessed; Xcelium item-level hole normalization remains a separate milestone.

### UVM Lifecycle Normalization

The UVM log ingestion layer remains simulator-independent. In addition to severity/test
metadata, it recognizes standard phase-trace report IDs emitted by `+UVM_PHASE_TRACE`
and objection-trace reports tagged `OBJTN_TRC` by `+UVM_OBJECTION_TRACE`. Normalized
phase and objection events retain log-line/time evidence and are persisted in dedicated
SQLite tables alongside the source report messages. When a report component explicitly
contains a `sequencer@@sequence` context, ZDDV records that as conservative sequence
report evidence linked to the original message.

There is no equivalent single portable sequence-state trace switch with one stable report
contract. ZDDV therefore keeps sequence **state** lifecycle evidence in a separate explicit
normalized JSON model instead of inferring start/end semantics from report text. That layer
validates the UVM state order, preserves sequence IDs, sequencer paths and parent IDs, permits
partial traces, correlates snapshots with recorded runs, and persists state events in separate
SQLite tables. ZDDV also accepts explicit normalized sequence-item handshake evidence (`GRANT`,
`REQUEST`, `ITEM_DONE`, and optional `RESPONSE`), validates ordering and stable identity, correlates
item snapshots with recorded runs, and persists both snapshot summaries and per-event evidence in
SQLite with `uvm-item-history` filtering. An opt-in UVM 1800.2 base-sequence adapter can now
emit lifecycle plus standard `start_item`/`finish_item`/`get_response` markers automatically once
a sequence adopts the generated base class; it uses public virtual UVM APIs and does not patch
`uvm_pkg`. Arbitration analysis already validates explicit FIFO/strict-priority evidence when the
trace supplies a UVM arbitration mode, while random/weighted/user winner choice remains
observational. Direct low-level `wait_for_grant`/`send_request` flows, response-handler callbacks,
hidden queues, lock/grab state, and vendor transcript semantics are still not inferred. See
`docs/uvm-sequence-trace.md`, `docs/uvm-item-trace.md`, `docs/uvm-arbitration-trace.md`, and
`docs/uvm-auto-instrumentation.md`.

## Next Architectural Steps

1. Add test/seed/plusarg models.
2. Add regression scheduler and worker pool.
3. Move run records into SQLite while preserving JSON artifacts.
4. Define normalized assertion and coverage schemas.
5. Add exact elaborated signal/port drivers and loads; generated hierarchy is already consumed by waveform cross-probing.
6. Add protocol-aware analyzers.
7. Add formal adapters.
8. Add AI-assisted triage over normalized ZDDV evidence.

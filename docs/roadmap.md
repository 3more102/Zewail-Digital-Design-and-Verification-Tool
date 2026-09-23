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
simulator-resolved elaborated hierarchy, VCD waveform indexing, explicit opt-in
FST indexing/probing/cross-probing/assertion correlation through `fst2vcd`,
targeted waveform value-change probing, source-level structural drivers/loads navigation,
waveform-to-source cross-probing with persisted elaborated/generated hierarchy
preference and simulator-elaborated instance qualification, documented Verilator
module-port direction evidence surfaced without unsupported schema inference, and
conservative direct CELL-pin-to-VARREF elaborated connectivity with complex expressions
retained as unsupported evidence plus bidirectional waveform cross-probing across exact
normalized pin bindings, fail-closed pin-binding schema validation with unsupported
child-pin query evidence retained separately, direction-qualified boundary driver/load
roles for trusted direct bindings, assertion-to-waveform correlation, and the
first protocol transaction reconstruction path (normalized APB traces) are
implemented. Exact elaborated drivers/loads remain future enrichment work.

- [x] Design hierarchy (source-level + simulator-elaborated).
- [x] Source indexing.
- [x] Waveform index (VCD scopes/signals; FST artifact metadata by default).
- [x] Explicit opt-in FST scope/signal indexing and bounded probing through `fst2vcd`.
- [x] Waveform-to-source cross-probing (VCD direct; FST through explicit `fst2vcd`).
- [x] Identity-validated persisted elaborated/generated hierarchy in waveform cross-probing.
- [x] Targeted waveform value-change probing (VCD direct; FST through explicit `fst2vcd`, time-windowed, bounded).
- [x] Drivers/loads navigation (source-level structural evidence).
- [x] Cross-probe connectivity instance qualification when valid elaborated hierarchy is available.
- [x] Evidence-gated Verilator JSON module-port normalization from documented `ioDirection` only; legacy XML remains explicitly unavailable.
- [x] Elaborated module-port direction/source evidence in waveform cross-probing and the Desktop elaborated browser.
- [x] Simulator-elaborated direct CELL pin-to-VARREF connectivity with explicit unsupported-expression evidence.
- [x] Bidirectional waveform cross-probing across normalized parent-signal/child-pin bindings with evidence-gated port direction.
- [x] Fail-closed normalized pin-binding schema validation and explicit unsupported child-pin query evidence without inferred parent relationships.
- [x] Exact direct pin-binding to source instance-port edge correlation without promoting source roles to elaborated semantics.
- [x] Generated-instance source-edge correlation when a direct pin selects one path from an explicitly enumerated ambiguous child-candidate set.
- [x] Direction-qualified boundary driver/load roles for direct normalized pin bindings; unavailable direction remains explicitly unclassified.
- [x] Assertion-to-waveform correlation (run/artifact link plus exact-name signal hints; FST through explicit `fst2vcd`).
- [x] Protocol transaction reconstruction (APB normalized-trace foundation).
- [x] Waveform-driven protocol extraction (APB VCD clock-edge sampling).

## v0.5 — Protocol Verification

Current status: normalized-trace analyzers are implemented for APB, AXI4-Lite,
burst-aware AXI4, async-FIFO CDC invariants, and a public-facts-based UCIe FLIT/link-health
foundation. Core AXI4 exclusive-access semantics plus address-sideband width and AxREGION 4KB
consistency checks, reserved AxCACHE encoding validation, B/M plus direction-aware
Allocate/Other-Allocate memory-class evidence (with legacy RA/WA bit compatibility),
optional USER-sideband transport evidence with explicit per-signal width/presence metadata,
explicit ID_W_WIDTH/ID_R_WIDTH transaction-ID presence and range evidence, explicit
ADDR_WIDTH address-range and VCD-width evidence, data-width-aware AxSIZE/WSTRB byte-lane
validation, and explicit absent-master-signal default normalization are implemented;
exhaustive optional AXI4 sideband/coherency semantics,
structural CDC signoff, and specification-complete UCIe checking remain planned.

- [x] APB normalized-trace reconstruction and protocol checks.
- [x] AXI4-Lite five-channel transaction reconstruction and protocol checks.
- [x] AXI4 burst normalized-trace foundation (IDs, lengths/types, WLAST/RLAST, 4KB rule).
- [x] AXI4 burst VCD extraction with timestamped transaction reconstruction.
- [x] AXI4 exclusive-access size/alignment, response, timing, and observed-pair checking.
- [x] AXI4 AxCACHE/AxPROT/AxQOS/AxREGION width checks, AxPROT privilege/security/access decoding, and AxREGION 4KB consistency.
- [x] AXI4 reserved AxCACHE encoding validation and B/M/RA/WA raw-bit decoding.
- [x] AXI4 direction-aware AxCACHE Allocate/Other-Allocate semantics and memory-class evidence.
- [x] AXI4 AWUSER/WUSER/BUSER/ARUSER/RUSER capture and channel-stability checking.
- [x] AXI4 explicit USER width/presence validation from `user_signal_widths`, including raw physical absence for width zero and required evidence on every corresponding VALID cycle.
- [x] AXI4 USER_REQ_WIDTH/RUSER composition consistency and VCD USER-width evidence capture.
- [x] AXI4 USER configuration/response-bit guidance as non-failing advisory evidence.
- [x] AXI4 ID_W_WIDTH/ID_R_WIDTH metadata validation plus complete-pair VCD width evidence for AWID/BID and ARID/RID; omitted/partial declarations remain unknown.
- [x] AXI4 ADDR_WIDTH metadata/range validation and matching AWADDR/ARADDR VCD width evidence.
- [x] AXI4 data-width-aware AxSIZE and WSTRB byte-lane legality checks.
- [x] AXI4 explicit absent-master-signal defaults for AxID/AxREGION/AxLEN/AxSIZE/AxBURST/AxLOCK/AxCACHE/AxQOS and WSTRB.
- [ ] Exhaustive AXI4 optional-sideband/coherency-adjacent semantics.
- [x] Asynchronous FIFO / CDC-oriented normalized-event invariant checks.
- [x] UCIe public 68B/256B FLIT trace and link-health foundation.
- [x] UCIe public-generation metadata with version-aware 32/64 GT/s ceiling checks through UCIe 3.0.
- [ ] Specification-complete UCIe protocol/PHY conformance checking.

Each pack should support reusable checks, assertions, transaction extraction, coverage goals, and debug summaries.

## v0.6 — UVM and Commercial Simulator Adapters

Current status: the simulator-independent UVM report-log ingestion foundation and the
first commercial-simulator adapter foundation are implemented. ZDDV extracts standard UVM
report messages, test names, severity summaries, report IDs, components, source locations, and
timestamps; persists normalized snapshots/messages in SQLite; correlates snapshots with recorded
simulation runs; and exposes `uvm-analyze` plus `uvm-history`. The Questa foundation supports
native compile/run orchestration, seeds/tests/plusargs, timeouts, optional VCD capture, assertion
ingestion, run-linked UVM ingestion, and per-run native UCDB coverage capture. Multi-run UCDB merge plus documented
`vcover report -summary` normalization into ZDDV coverage history are implemented. Ordinary
covergroup bins from `vcover report -cvg -details` are also normalized into the existing
functional-coverage database. Machine-readable XML detail output, complementary zero-hit source/file-line evidence, multibit-expression evidence, and a dedicated by-instance toggle detail report are retained when available. Documented statement and branch detail, scalar condition/expression FEC rows, multibit-expression input-term-bit rows, FSM state/transition rows, and native by-instance binary/extended toggle transition counters are normalized into the shared coverage-hole model; multibit-condition and enumerated/unknown toggle layouts remain evidence-only until their exact native schemas are verified.
Phase/objection lifecycle normalization, explicit sequence report-context evidence, normalized sequence state lifecycle ingestion, and normalized sequence-item handshake persistence/history are implemented. The portable opt-in UVM base-sequence adapter now emits lifecycle and standard item-handshake evidence automatically through public UVM virtual APIs, while explicit arbitration-mode evidence drives FIFO/strict-priority checks and project-defined fairness analysis. Low-level direct handshake APIs, response-handler callbacks, hidden queues, lock/grab state, and random/weighted/user winner prediction remain outside the evidence boundary.

- [x] Questa execution adapter foundation.
- [x] Questa native per-run UCDB coverage capture.
- [x] Questa UCDB merge and summary-level ZDDV coverage reporting.
- [x] Questa ordinary functional covergroup-bin normalization and functional hole queries.
- [x] Questa detailed XML coverage evidence export.
- [x] Questa complementary zero-hit source-detail evidence export.
- [x] Questa dedicated by-instance toggle detailed evidence capture.
- [x] Questa statement/branch plus scalar condition/expression FEC item/source normalization and coverage-hole reporting.
- [x] Questa documented multibit-expression FEC input-term-bit normalization and coverage-hole reporting.
- [x] Questa FSM state/transition item-level normalization and coverage-hole reporting.
- [x] Questa by-instance binary/extended toggle transition item-level normalization from native XML.
- [x] Questa evidence-preserving detail-schema audit with exact artifact SHA-256 fingerprints, existing-parser point counts, and explicit enumerated/unknown toggle-tag inventory without inferred semantics.
- [ ] Questa multibit-condition and enumerated/unknown toggle item-level normalization pending verified native schemas.
- [x] VCS execution adapter foundation (build/run, seed/test/plusargs, VCD, assertions, run-linked UVM).
- [x] VCS native per-run coverage database capture.
- [x] VCS multi-run URG merge and report evidence retention.
- [x] VCS normalized URG dashboard score ingestion and percentage-native history snapshots.
- [x] VCS documented global covergroup type/instance covered/expected count ingestion.
- [x] VCS documented module-level line/branch covered/total count ingestion from `modinfo.txt`.
- [x] VCS instance-level line/condition/toggle/branch covered/total ingestion plus separate FSM state/transition/sequence counts from URG HTML detail reports.
- [x] VCS module-level line/condition/toggle/FSM-transition/branch covered/total count ingestion from `modinfo.txt`, with scored FSM transitions kept distinct from non-scored states.
- [x] Xcelium execution adapter foundation (xrun elaborate/run, seed/test/plusargs, VCD, assertions, run-linked UVM).
- [x] Xcelium native per-run coverage database capture.
- [x] Xcelium native runfile + `union_all` multi-run IMC merge with merged `.ucm`/`.ucd` validation and retained diagnostics.
- [x] Xcelium cumulative Overall/Code/FSM/Functional Average/Covered metric normalization into percentage-native ZDDV coverage history.
- [x] Xcelium explicitly reported two-field covered/total count persistence without percentage-derived counts.
- [x] Xcelium IMC detailed all-metrics/source report evidence capture, with tool failures retained without fabricating item data.
- [x] Xcelium verified block/expression/toggle item-level normalization persisted in `.zddv/coverage/xcelium/items.json` and consumed by coverage-hole queries.
- [x] Xcelium native IMC detail-section schema audit with source/section SHA-256 evidence, schema-neutral lexical layout fingerprints, and explicit unverified-layout classification.
- [ ] Xcelium FSM/functional item-level coverage-hole normalization pending verified native schemas.
- [x] Simulator-independent UVM report/test metadata ingestion and SQLite persistence.
- [x] UVM snapshot-to-run correlation with run-log resolution and history filtering.
- [x] Phase/objection-aware UVM lifecycle trace normalization and SQLite persistence.
- [x] Explicit `sequencer@@sequence` report-context evidence where present.
- [x] Normalized sequence state lifecycle reconstruction through an explicit JSON contract.
- [x] Normalized sequence-item handshake analysis with run correlation, SQLite event/violation persistence, history filtering, and violation queries.
- [x] Observed grant order plus explicit ARB_REQUEST contention/bypass evidence and opt-in bypass bounds.
- [x] Opt-in automatic sequence/item instrumentation adapter plus evidence-gated UVM arbitration-policy/fairness reconstruction.

## v0.7 — Formal

- [x] Formal adapter API.
- [x] Bounded checks (SymbiYosys BMC with explicit finite depth).
- [x] Normalized property/cover result persistence and history.
- [x] Tool-specific formal result ingestion (native SymbiYosys logfile evidence).
- [x] Normalized counterexample/witness artifact model and JSON CLI ingestion.
- [x] Native VCD counterexample/witness normalization with signal selection and provenance.
- [x] Bounded SymbiYosys cover/reachability execution with machine-readable per-property covered/unreached history.
- [x] Cross-run formal cover coverage grouped by exact design/backend/engine/depth/property universe compatibility.
- [x] Normalized formal counterexample/witness signal cross-probing into RTL hierarchy, declarations, and source-structural connectivity.

## v0.8 — Intelligent Debug

- [x] Log/waveform/assertion correlation.
- [x] Failure clustering.
- [x] Deterministic root-cause candidate ranking from explicit run/assertion/waveform/RTL evidence.
- [x] Suggested next debug probes derived only from explicit ranked failure evidence.
- [x] Coverage-hole test suggestions derived only from normalized explicit hole evidence.
- [x] Generated assertion/test artifacts staged for review and applied only by explicit SHA-confirmed opt-in.

## v0.9 — AI-Assisted Debug Foundation

- [x] Provider-neutral RCA context bundle composed only from deterministic ZDDV evidence.
- [x] Canonical evidence SHA-256 provenance for downstream review.
- [x] Explicit policy flags disabling automatic external transmission, model invocation, and command execution.
- [x] Opt-in pluggable model-provider adapters with explicit external-transmission gating.
- [x] Schema-validated model-response ingestion with strict evidence-reference resolution and request/context provenance checks.
- [x] SHA-confirmed human review gate before reviewed model proposals can enter the existing generated-artifact staging/apply workflow.
- [x] Read-only provenance-chain audit that re-verifies context evidence, provider-request, raw-response, validated-payload, and optional human-review hashes without model invocation.

## v1.0 — Reproducible AI Review Workflow

- [x] Offline/manual AI response import without model invocation or external transmission.
- [x] Imported response bytes bound to the exact model-request and context-evidence SHA-256 provenance.
- [x] End-to-end CI trust-chain coverage from offline import through validation, SHA-bound review, proposal export, and review-isolated staging.
- [x] Portable audit-bundle export for the complete context/raw/validated/review/proposal provenance chain.

## v1.0 — Verification Signoff

Current status: the first deterministic signoff-review bundle is implemented. It
aggregates selected persisted simulation evidence plus the latest normalized coverage,
formal, and UVM snapshots; records canonical evidence/policy/signoff SHA-256 provenance;
and exposes conservative blocking rules for known failures, required missing evidence,
and optional coverage thresholds. READY_FOR_REVIEW is explicitly not a claim that
uncollected verification objectives passed.

- [x] Deterministic signoff review bundle over persisted verification evidence.
- [x] Canonical evidence, policy, and signoff SHA-256 provenance.
- [x] Conservative simulation failure/missing-evidence blocking.
- [x] Optional coverage-presence and minimum-percentage gate.
- [x] Optional formal/UVM evidence requirements while still blocking explicit failures.
- [x] CI-friendly signoff command exit status and JSON artifact.
- [x] Explicit snapshot/run ID pinning for release-candidate manifests.
- [x] Signed release manifest and reproducible archive export (Ed25519, exact signoff-SHA confirmation, deterministic ZIP).

## v1.1 — Signoff Change Review

- [x] Fail-closed validation of baseline/current signoff provenance before comparison.
- [x] Deterministic policy/evidence/check diff with its own canonical SHA-256.
- [x] Exact simulation run added/removed/changed reporting.
- [x] Coverage/formal/UVM evidence-change summaries without quality inference.
- [x] CLI JSON artifact for release/review change inspection.

## v1.1 — Desktop Debug Studio

Current status: evidence browsing, source preview, hierarchy navigation, and waveform
inspection remain read-only and reuse authoritative ZDDV core models. Mutating or
simulator-invoking desktop operations are isolated behind explicit SHA-confirmed review
gates; the GUI does not automatically invoke AI or execute generated code.

- [x] Tk/ttk desktop shell with verification summary.
- [x] Recent-run browser backed by persisted run records.
- [x] Failure-group browser backed by normalized failure signatures.
- [x] Latest normalized coverage, assertion, formal, and UVM evidence overview.
- [x] Refresh from the shared backend without duplicating verification logic.
- [x] Source/hierarchy navigation panes backed by the deterministic in-memory design index.
- [x] Persisted simulator-elaborated hierarchy browser without launching elaboration from the GUI.
- [x] Read-only waveform navigation, bounded targeted probing, and hierarchy/source/connectivity cross-probe integration.
- [x] Detailed assertion/UVM/formal evidence panes with bounded persisted-evidence queries.
- [x] Review-gated lint/build/run project actions using exact SHA-256 approval, project/source revalidation, and existing core APIs.
- [x] Review-gated recorded-input historical rerun using persisted test/seed/plusargs/timeout evidence, with the selected run record bound into the reviewed SHA-256 payload, the current configured backend rebuilt, and the historical command retained as evidence rather than replayed verbatim.

## v1.2 — Desktop Review Workflows

Current status: generated verification drafts can now be inspected and applied from a
separate desktop review pane without bypassing the existing generated-artifact core
gates. Apply does not compile or run the generated SystemVerilog.

- [x] Enumerate staged generated assertion/test drafts from the isolated draft store.
- [x] Re-hash staged bytes and show a bounded exact-content preview.
- [x] Fail closed unless review is required, auto-apply is disabled, and execution is disabled.
- [x] Require manual exact content SHA-256 plus explicit reviewed approval before apply.
- [x] Delegate staging/apply to the existing core APIs and refuse changed, malformed, or already-applied drafts.
- [x] Keep generated-code compilation/simulation separate from the apply action.

## Long-Term Direction

The long-term target is a unified digital design and verification environment rather than a simulator clone: one project model and verification database across simulation, regression, assertions, coverage, protocols, waveform debug, formal, and intelligent triage.

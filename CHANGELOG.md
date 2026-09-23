# Changelog

## Unreleased

### Elaborated internal connectivity

- Normalize module-root Verilator `ASSIGNW` records only when both sides are plain `VARREF` expressions.
- Expose exact continuous-assignment driver/load edges in cross-probing as a separate simulator-elaborated internal layer.
- Preserve complex assignment sides as unresolved reference evidence and mark affected queries partial instead of inferring connectivity.
- Keep legacy XML, generated-scope, procedural, select, concatenation, and other complex-expression semantics outside the normalized contract.
- Surface that exact internal connectivity in Desktop cross-probing with bounded rows and fail-closed contract/list validation.
- Harden Desktop internal ASSIGNW rendering with exact evidence-contract/query identity checks, per-item semantic validation, and NORMALIZED/PARTIAL status consistency.

### Elaborated debug correlation

- Extend direct pin-to-source edge correlation to generated instances only when
  the exact elaborated child path is already present in the source edge's
  explicit ambiguous candidate set; preserve source-only driver/load semantics.
- Harden Desktop rendering of that correlation against the current core producer shape
  and analysis envelope; preserve source instance-port identity, structural driver/load
  role semantics, and exact `match_basis` provenance; fail closed on malformed, empty,
  mismatched-envelope, or future-status payloads.

### Elaborated boundary connectivity

- Validate the trusted persisted direct pin-binding contract and item semantics before cross-probing, including expression status, endpoint identity, generated-scope shape, and unsupported-count consistency.
- Classify direct normalized parent-signal/child-pin bindings as boundary drivers or loads using only normalized module-port direction evidence.
- Keep unavailable or ambiguous direction explicitly unclassified, and keep unsupported complex pin expressions outside driver/load role inference.
- Preserve the boundary-only scope: this does not claim complete simulator-elaborated internal drivers or loads.
- Surface the core-provided boundary driver/load/unclassified buckets in Desktop waveform cross-probing with bounded rows and no GUI-side role inference.

### AXI4 USER presence evidence

- Require every configured non-zero USER signal on each corresponding channel VALID cycle, including stalls, using raw normalized-trace sample evidence.
- Treat any raw observation of a USER signal declared width zero as a physical-interface contradiction.
- Keep unconfigured USER signals optional and do not infer widths or presence from transaction values.

### AXI4 USER guidance evidence

- Report Arm USER width maxima and USER data-width granularity as explicit non-failing advisories when sufficient interface metadata is present.
- Report changing lower RUSER response bits across multi-beat reads as recommendation evidence without changing the protocol PASS/FAIL verdict.
- Preserve the distinction between mandatory AXI4 legality checks and specification guidance/recommendations.

### FST debug correlation

- Extend the explicit `fst2vcd` adapter to `crossprobe` and `assertion-waveform`; FST stays metadata-only unless the user opts in.
- Preserve converter provenance and the `indexed-via-fst2vcd` parse status in debug evidence.
- Reuse one converted waveform index per run during assertion correlation; converter failures remain fail-closed.

### Xcelium coverage evidence

- Add schema-neutral lexical layout fingerprints for captured IMC detail sections using only structured delimiters, field counts, and token classes; unverified FSM/functional layouts remain evidence-only and are not normalized.
- Show the layout fingerprint in `xcelium-detail-audit` output alongside the exact section-content SHA-256.

### Desktop Debug Studio

- Add bounded detail panes for normalized assertion events, formal property results,
  and UVM report messages while preserving source, source-level hierarchy, and
  persisted simulator-elaborated hierarchy views.
- Add optional query limits to formal-property and UVM-message storage readers so the
  desktop does not materialize an unbounded evidence set.
- Keep refresh display-only with respect to verification execution, AI invocation,
  and generated-artifact application.
- Add SHA-confirmed recorded-input historical rerun, binding the selected persisted
  run record and its recorded test/seed/plusargs/timeout into the reviewed action
  payload. Execution rebuilds the current configured backend and replays those
  recorded runtime inputs; the historical command is review evidence and is not
  executed verbatim.

### Release verification hardening

- Require the signed manifest `project`, `simulator`, and `top` identity to match
  the bundled verified signoff exactly.
- Reject signed manifests whose `key_id` is empty or non-canonical due to leading or
  trailing whitespace.
- Reject an otherwise-valid signoff before signing when its project, simulator, or
  top-level identity differs from the active project configuration.

### Signoff change review

- Add deterministic `signoff-diff` comparison for two validated signoff bundles.
- Recompute evidence, policy, and signoff SHA-256 values before accepting either input.
- Report exact policy keys, run-set changes, normalized evidence changes, check changes,
  review-state transitions, and blocker additions/removals without quality inference.
- Fingerprint the complete diff artifact with a canonical SHA-256.

## 1.0.0 — 2026-09-22

ZDDV 1.0.0 marks the first release milestone in which the verification evidence,
review workflow, and release artifacts are designed to be reproducible and
auditable end to end.

### Verification and signoff

- Deterministic verification signoff bundles over persisted simulation, coverage,
  formal, and UVM evidence.
- Explicit run and snapshot pinning for release-candidate signoff manifests.
- Conservative blocking for known simulation/formal/UVM failures, missing required
  evidence, and optional minimum-coverage thresholds.
- Canonical evidence, policy, and signoff SHA-256 provenance.
- Reproducible release ZIP export with fixed archive metadata and exact reviewed
  signoff-SHA confirmation.
- Ed25519-signed release manifests with separate trusted-public-key verification.

### AI-assisted review workflow

- Provider-neutral deterministic RCA context bundles.
- Opt-in provider invocation with external-transmission gating.
- Offline/manual response import with no provider invocation.
- Strict response-schema and evidence-reference validation.
- SHA-confirmed human review before generated proposals can enter the staging flow.
- Read-only provenance-chain auditing.
- Portable deterministic audit bundles spanning context, raw response, validated
  response, approved review, and selected reviewed proposals.
- Relocation-safe bundle verification based on bundled content and provenance hashes
  rather than original absolute project paths.
- Exact validated-payload proposal-index provenance on reviewed proposal exports.

### Verification foundation retained in 1.0

The release continues to include the existing RTL project model, simulation and
regression orchestration, assertion ingestion, coverage persistence and analysis,
waveform/debug tooling, APB/AXI/UCIe analysis, UVM normalization and instrumentation,
formal evidence ingestion, generated-artifact review isolation, and the supported
simulator adapter foundations documented in the README.

### Trust boundaries

- `READY_FOR_REVIEW` means the selected persisted evidence satisfies the configured
  ZDDV signoff checks. It is not a claim that uncollected verification objectives
  passed or that a design is specification-complete.
- An Ed25519-valid release authenticates the signed manifest under the trusted public
  key and binds the exact signoff hashes; it does not independently prove design
  correctness.
- A VERIFIED portable AI audit bundle proves internal provenance consistency of its
  bundled artifacts. SHA-256 alone is not an authenticity signature and does not
  establish that model hypotheses or generated code are correct.
- Generated verification artifacts remain review-gated and are not automatically
  staged, applied, compiled, or executed by the AI workflow.

### CI qualification

The v1.0 release branch is required to pass the unit-test matrix on Python 3.11,
3.12, and 3.13, build a wheel matching package metadata, and pass the Verilator
integration workflow before merge.

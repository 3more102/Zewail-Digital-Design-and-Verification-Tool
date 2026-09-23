# Zewail Digital Design and Verification Tool (ZDDV)

[![ZDDV CI](https://github.com/3more102/Zewail-Digital-Design-and-Verification-Tool/actions/workflows/ci.yml/badge.svg)](https://github.com/3more102/Zewail-Digital-Design-and-Verification-Tool/actions/workflows/ci.yml)

ZDDV is an open digital design and verification environment for RTL development, simulation orchestration, regression, coverage, waveform artifacts, assertion, protocol, UVM, formal, and future AI-assisted verification workflows.

> Status: **v1.0 — reproducible verification signoff with exact evidence pinning and Ed25519-signed release archives, plus a review-gated AI workflow with portable provenance audit bundles**

## What Works Today

- TOML-based verification projects
- RTL/testbench source discovery
- Simulator-adapter architecture
- Formal adapter API with normalized check/property/result contracts, persisted evidence history, finite-depth SymbiYosys BMC execution, direct cover-property reachability, automatic native VCD counterexample/witness normalization, and formal-trace-to-RTL cross-probing
- Verilator detection and version reporting
- Questa/QuestaSim native `vlib`/`vlog`/`vsim` build/run foundation with version detection, seeds, plusargs, timeouts, VCD capture, assertion ingestion, and run-linked UVM normalization
- Synopsys VCS native `vcs` -> `simv` build/run foundation with version detection, UVM 1.2 compilation, deterministic seeds, plusargs, timeouts, VCD capture, assertion ingestion, run-linked UVM normalization, per-run native `.vdb` coverage capture, multi-run URG merge/report evidence, normalized dashboard scores, documented global covergroup type/instance counts, documented module-level line/branch covered/total counts from `modinfo.txt`, and deduplicated instance-level line/condition/toggle/branch plus separate FSM state/transition/sequence counts from URG HTML detail
- Cadence Xcelium native `xrun -elaborate` / `xrun -R` foundation with deterministic seeds, plusargs, timeouts, VCD capture, assertion/UVM ingestion, isolated per-run native coverage databases, IMC multi-run merge/report evidence, normalized Overall Average/Covered score history, and conservative item-level block/expression/toggle `coverage-holes` from verified native detail tables, plus an evidence-preserving `xcelium-detail-audit` that inventories native report sections and records content plus schema-neutral lexical layout fingerprints for unverified FSM/functional layouts before parser support is added
- Questa per-run UCDB capture, multi-run `vcover merge`, normalized `vcover report -summary` metrics, ordinary covergroup-bin ingestion, complementary XML/zero-hit evidence, and source-linked `coverage-holes` for statement/branch, scalar condition/expression FEC, documented multibit-expression input-term-bit FEC, FSM state/transition items, and native by-instance binary/extended toggle transitions; multibit-condition and enumerated/unknown toggle layouts remain evidence-only until their exact native schemas are verified
- Evidence-preserving Questa detail-schema audit across captured text/XML artifacts, with exact SHA-256 fingerprints, existing-parser point counts, explicit pending multibit-condition targets, and enumerated/unknown toggle-tag inventory without inferred semantics
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
- Deterministic coverage-hole test-intent suggestions that retain source evidence, require review, and never execute stimulus automatically
- Review-gated generated SystemVerilog assertion/test artifacts with isolated staging, SHA-256 provenance, explicit apply approval, overwrite refusal, and no automatic execution
- Provider-neutral AI-assisted RCA context bundles with deterministic evidence hashing, explicit uncertainty rules, and no automatic external transmission or model invocation
- Opt-in model-provider invocation with strict response-schema/evidence-reference validation, SHA-confirmed human review, and no automatic code staging or execution
- Read-only AI provenance-chain auditing across context, provider request/response, validated payload, and optional human review artifacts
- Deterministic portable AI audit bundles spanning context, raw/validated responses, approved review, and exact reviewed proposals, with relocation-safe SHA-256 verification
- Deterministic verification signoff bundles with explicit evidence/run/snapshot pinning, conservative READY_FOR_REVIEW semantics, and reproducible Ed25519-signed release archives
- Deterministic fail-closed signoff-to-signoff change review with policy/evidence/check diffs and canonical diff SHA-256 provenance
- Normalized assertion result database keyed by simulation run
- Simulator-independent UVM report-log normalization with test-name discovery, severity summaries, source/report metadata, SQLite persistence, run correlation, and history CLI
- UVM phase/objection lifecycle normalization from standard `+UVM_PHASE_TRACE` / `+UVM_OBJECTION_TRACE` report evidence, plus conservative `sequencer@@sequence` report-context evidence, persisted in SQLite
- Simulator-independent UVM sequence **state** lifecycle JSON model with transition validation, nested parent/sequencer evidence, partial-trace handling, run correlation, SQLite snapshots, history CLI, explicit log-marker ingestion, and a generated portable SystemVerilog instrumentation helper
- Simulator-independent UVM sequence-item handshake normalization with run correlation, SQLite snapshot/event persistence, history filtering, explicit log-marker ingestion, and a generated portable SystemVerilog instrumentation helper
- Opt-in UVM 1800.2 base-sequence adapter that automatically emits sequence lifecycle and standard `start_item`/`finish_item`/`get_response` evidence without patching `uvm_pkg` or inferring hidden sequencer state
- Simulator-independent functional coverage snapshots and per-bin database
- APB normalized-trace transaction reconstruction with wait-state and protocol-violation analysis
- APB transaction extraction directly from VCD waveforms at configurable clock edges
- AXI4-Lite transaction extraction directly from VCD waveforms with five-channel handshake sampling
- AXI4-Lite normalized-trace reconstruction with independent channel handshake and backpressure checks
- AXI4 burst analysis with IDs, lengths/types, WLAST/RLAST, 4KB-boundary checks, core exclusive-access semantics, direction-aware AxCACHE Allocate/Other-Allocate memory-class evidence, AxPROT/AxQOS/AxREGION validation, explicit ID_W_WIDTH/ID_R_WIDTH presence and range evidence, optional AWUSER/WUSER/BUSER/ARUSER/RUSER transport evidence with explicit interface-width validation, and data-width-aware WSTRB byte-lane checks
- Asynchronous-FIFO CDC dynamic invariant analysis for local binary/Gray pointers and full/empty blocking behavior
- Burst-aware AXI4 transaction extraction directly from VCD waveforms with timestamp preservation
- Public-facts-based UCIe 68B/256B FLIT trace and link-health analysis with ACK/NAK and CRC summaries, plus optional version-aware public data-rate ceiling evidence through UCIe 3.0
- Compatibility path for packaged Verilator 5.020 coverage generation
- SQLite verification results database and run history
- Desktop Debug Studio using Python Tk/ttk with read-only evidence/source/waveform navigation, revision-validated elaborated hierarchy, SHA-confirmed lint/build/run actions, and exact-SHA generated-artifact review/apply with no automatic generated-code execution
- Selective rerun of historical PASS / FAIL / TIMEOUT runs
- JUnit XML export for CI systems
- Failure-signature normalization and grouping across failing seeds
- CI on Python 3.11, 3.12, and 3.13
- End-to-end Verilator CI examples, including a dual-clock asynchronous FIFO
- SystemVerilog assertions enabled in Verilator build/lint flows
- Deterministic source index with file hashes and source locations
- Source-level module/interface hierarchy with recursive-cycle protection
- Simulator-elaborated hierarchy normalized from Verilator JSON/XML parser output
- VCD waveform scope/signal index with FST artifact metadata by default and explicit opt-in `fst2vcd` scope/signal indexing
- Targeted VCD signal value probing plus explicit opt-in FST probing through `fst2vcd`, with exact/unique-name resolution, time windows, and bounded change capture
- Assertion-to-waveform run correlation with conservative signal hints and explicit opt-in FST indexing through `fst2vcd`
- Source-level structural drivers/loads navigation with assignment and instance-port evidence
- Waveform-to-RTL source cross-probing with hierarchy-aware signal resolution and explicit opt-in FST indexing through `fst2vcd`

## Quick Start

Requirements:

- Python 3.11+
- Verilator available in `PATH` for the default backend
- Optional Questa/QuestaSim: `vlib`, `vlog`, `vsim`, and `vcover` available in `PATH` for native UCDB coverage workflows
- Optional Synopsys VCS: `vcs` available in `PATH`
- Optional Cadence Xcelium: `xrun` available in `PATH`

Install ZDDV for development:

```bash
git clone https://github.com/3more102/Zewail-Digital-Design-and-Verification-Tool.git
cd Zewail-Digital-Design-and-Verification-Tool
python -m pip install -e ".[dev]"
zddv doctor
zddv doctor --simulator questa
zddv doctor --simulator vcs
zddv doctor --simulator xcelium
```

Run the included counter example:

```bash
zddv --project examples/counter build
zddv --project examples/counter run --test counter_basic --seed 42
zddv --project examples/counter regress examples/counter/regression.toml
zddv --project examples/counter coverage

zddv --project examples/async_fifo lint
zddv --project examples/async_fifo run --test async_fifo_smoke --seed 42
zddv --project examples/async_fifo regress examples/async_fifo/regression.toml
zddv --project examples/async_fifo coverage
zddv --project examples/async_fifo gui
```

For a Questa project, setting `coverage = true` in `[run]` enables native
coverage instrumentation and writes `coverage.ucdb` inside each run directory.
`zddv coverage` then merges run UCDBs with `vcover merge`, runs
`vcover report -summary`, and stores normalized aggregate metrics in the same
SQLite coverage-history model. It also runs `vcover report -cvg -details` and,
when ordinary covergroup bins are present, ingests them into ZDDV's existing
functional-coverage database. `zddv fcov-history` and `zddv fcov-holes` can then
inspect those normalized bins. ZDDV also retains complementary detailed code-coverage
evidence: XML output for machine-readable follow-up, a `-zeros -details` report
for zero-hit source/file-line evidence, documented multibit-expression detail,
and dedicated by-instance toggle text/XML detail. ZDDV normalizes documented
statement and branch rows, scalar condition/expression FEC rows, documented
multibit-expression FEC input-term bits, FSM state/transition rows, and native
by-instance binary/extended toggle transition counters into `zddv coverage-holes`.
The toggle XML path preserves instance scope and individual transition counts;
enumerated or unknown toggle layouts and multibit-condition layouts remain raw
evidence until their exact native schemas are verified. Questa's weighted total
coverage remains a separate simulator-reported value.

For a VCS project with `coverage = true`, ZDDV instruments compilation and simulation with `-cm line+cond+fsm+tgl+branch` and directs each run to its own `coverage.vdb` using `-cm_dir`. The per-run database is recorded only when it actually exists. `zddv coverage` then uses Synopsys URG to merge all per-run VDBs into `.zddv/coverage/coverage.vdb` and retain an `urg-report` directory. When `dashboard.txt` contains the documented Total Coverage Summary, ZDDV normalizes the overall SCORE plus available LINE/COND/TOGGLE/FSM/BRANCH/ASSERT/GROUP percentages and stores them in a percentage-native SQLite history. If the documented Total Groups Coverage Summary is present, ZDDV also stores its global covergroup type and instance COVERED/EXPECTED counts without converting percentages into synthetic counts. ZDDV also parses module-level Line, Conditions, Total Bits toggle, scored FSM Transitions, and Branch total/covered rows from `modinfo.txt` and persists them as explicitly scoped `module_line`, `module_condition`, `module_toggle`, `module_fsm`, and `module_branch` counts; these counts do not replace the design-wide dashboard percentages. FSM module counts use the scored transition rows rather than the non-scored state rows. ZDDV also aggregates explicitly reported instance-level Line, Cond, Total Bits toggle, and Branch covered/total rows from URG HTML detail pages, while retaining FSM state, transition, and sequence rows as separate metrics and deduplicating repeated/paginated instance evidence. If the dashboard is absent or unparseable, the merged VDB/report evidence remains available without a numeric snapshot.

For an Xcelium project, ZDDV uses the native `xrun` flow: `-elaborate` with a dedicated `-xmlibdirname` build database, followed by `xrun -R` for repeatable runs. The adapter carries `-svseed`, ZDDV/UVM test plusargs, timeout classification, assertion ingestion, run-linked UVM normalization, and optional VCD capture through an `-input` Tcl probe script. With `coverage = true`, build enables `-coverage all`; each run is assigned an isolated Cadence coverage hierarchy through `-covworkdir`, `-covscope`, and `-covtest`, and ZDDV records the run database only when a `.ucd` artifact is actually present. `zddv coverage` feeds the captured `.ucd` files to Cadence IMC through a runfile, merges them with `-initial_model union_all`, requires the native merged `.ucm` model plus `.ucd` data under `cov_work/scope/merged`, retains merge diagnostics and the exact report commands as evidence, and then loads that merged run for cumulative reporting. In addition to the summary, ZDDV captures a detailed all-metrics report with source evidence; a detail-report tool error is retained explicitly and does not cause ZDDV to invent item-level coverage. The documented cumulative `Overall`, `Code`, `FSM`, and `Functional` `Average`/`Covered` grades are preserved as separate percentage metrics; `Overall Covered` is the shared history score, and parenthesized two-field covered/total counts are persisted only when explicitly reported by IMC. Verified native IMC block, expression truth-row, and toggle-bit tables are normalized once into `.zddv/coverage/xcelium/items.json`, which `zddv coverage-holes` consumes preferentially; explicit expression `IGN` rows are not promoted to coverage goals. Unknown detailed-item layouts remain evidence-only. The detail audit fingerprints structured row shape from delimiter, field-count, and lexical token-class evidence without assigning vendor semantics; Xcelium FSM and functional item-level normalization remain pending.

## Current CLI

```bash
zddv init my_project

zddv --project my_project add rtl "rtl/*.sv"
zddv --project my_project add tb "tb/*.sv"

zddv --project my_project config simulator verilator
# or: zddv --project my_project config simulator questa
# or: zddv --project my_project config simulator vcs
zddv --project my_project config top tb_top

zddv doctor
zddv --project my_project index
zddv --project my_project hierarchy
zddv --project my_project elaborate
zddv --project my_project hierarchy --elaborated
zddv --project my_project connectivity
zddv --project my_project connectivity count --unit counter
zddv --project my_project waveform-index
zddv --project my_project waveform-index --run <run-id>
zddv --project my_project waveform-index --input trace.vcd
zddv --project my_project waveform-probe tb_top.dut.count --start 0 --end 1000
zddv --project my_project crossprobe tb_top.dut.count
zddv --project my_project crossprobe tb_top.dut.count --input trace.vcd
zddv --project my_project crossprobe tb_top.dut.count --input trace.fst --fst2vcd
zddv --project my_project assertion-waveform --status FAIL
zddv --project my_project assertion-waveform --status FAIL --fst2vcd
zddv --project my_project lint
zddv --project my_project build
zddv --project my_project formal-bmc --depth 20
zddv --project my_project formal-cover --depth 20
zddv --project my_project formal-vcd-trace trace.vcd --property top.p_safe --kind assert
zddv --project my_project formal-vcd-trace witness.vcd --property top.c_reached --kind cover --signal state
zddv --project my_project formal-crossprobe .zddv/formal/counterexamples/latest.json --signal state
zddv --project my_project formal-history --limit 20

zddv --project my_project run
zddv --project my_project run --test smoke --seed 100
zddv --project my_project run --plusarg +MODE=1 --timeout 30

zddv --project my_project regress regression.toml
zddv --project my_project runs --limit 20
zddv --project my_project rerun --status FAIL --status TIMEOUT --limit 20
zddv --project my_project junit --output .zddv/junit.xml --limit 100
zddv --project my_project failures --limit 200
zddv --project my_project signoff --run-id <release-run-id> --coverage-snapshot-id <release-coverage-id> --min-coverage 90
zddv --project my_project signoff-diff .zddv/signoff/baseline.json .zddv/signoff/current.json
zddv --project my_project release-export --expected-signoff-sha256 <reviewed-signoff-sha256> --private-key release-private.pem --key-id lab-release-2026
zddv --project my_project release-verify .zddv/signoff/release.zip --public-key release-public.pem
zddv --project my_project report --limit 100
zddv --project my_project coverage
zddv --project my_project coverage-history --limit 20
zddv --project my_project questa-detail-audit
zddv --project my_project xcelium-detail-audit
zddv --project my_project coverage-holes --show 20
zddv --project my_project coverage-holes --type line --output .zddv/coverage/line-holes.json
zddv --project my_project coverage-suggest --show 20
zddv --project my_project generated-stage proposal.json
zddv --project my_project generated-apply .zddv/generated/drafts/<draft-id>/manifest.json --expected-sha256 <reviewed-sha256> --approve-reviewed
zddv --project my_project ai-rca-context --run <run-id>
zddv ai-providers
zddv --project my_project ai-provider-request --context .zddv/debug/ai-rca-context.json
zddv --project my_project ai-provider-run --context .zddv/debug/ai-rca-context.json --provider openai-compatible --endpoint https://provider.example/v1/chat/completions --model provider-model --api-key-env PROVIDER_API_KEY --allow-external --expected-request-sha256 <reviewed-request-sha256>
zddv --project my_project ai-response-import --context .zddv/debug/ai-rca-context.json --content .zddv/ai/manual-response.json --provider-label offline-review
zddv --project my_project ai-response-ingest --response .zddv/ai/provider-response.json --context .zddv/debug/ai-rca-context.json
zddv --project my_project ai-response-review .zddv/ai/validated-response.json --expected-sha256 <validated-sha256> --approve-reviewed
zddv --project my_project ai-proposal-export .zddv/ai/reviews/<review-id>.json --proposal 1
zddv --project my_project ai-chain-audit --context .zddv/debug/ai-rca-context.json --response .zddv/ai/provider-response.json --validated .zddv/ai/validated-response.json --review .zddv/ai/reviews/<review-id>.json
zddv --project my_project ai-audit-bundle-export --context .zddv/debug/ai-rca-context.json --response .zddv/ai/provider-response.json --validated .zddv/ai/validated-response.json --review .zddv/ai/reviews/<review-id>.json --proposal .zddv/ai/proposals/<proposal>.json
zddv ai-audit-bundle-verify my_project/.zddv/ai/audits/ai-audit-bundle.zip
zddv --project my_project assertions --limit 100
zddv --project my_project assertions --status FAIL
zddv --project my_project uvm-analyze uvm.log --source questa
zddv --project my_project uvm-analyze --run <run-id>
zddv --project my_project uvm-history --run <run-id>
zddv --project my_project uvm-item-analyze item_trace.json
zddv --project my_project uvm-item-analyze item_trace.json --run <run-id>
zddv --project my_project uvm-item-history --limit 20
zddv --project my_project uvm-item-instrument
zddv --project my_project uvm-item-log-analyze simulation.log
zddv --project my_project uvm-item-log-analyze --run <run-id>
zddv --project my_project uvm-item-violations <snapshot-id> --code LATE_GRANT
zddv --project my_project uvm-sequence-instrument
zddv --project my_project uvm-auto-instrument
zddv --project my_project uvm-sequence-analyze sequence_trace.json
zddv --project my_project uvm-sequence-analyze sequence_trace.json --run <run-id>
zddv --project my_project uvm-sequence-log-analyze simulation.log
zddv --project my_project uvm-sequence-log-analyze --run <run-id>
zddv --project my_project uvm-sequence-history --limit 20
zddv --project my_project fcov-import functional_coverage.json
zddv --project my_project fcov-history --limit 20
zddv --project my_project fcov-holes --limit 50
zddv --project my_project apb-analyze apb_trace.json
zddv --project my_project apb-waveform --input apb.vcd
zddv --project my_project apb-waveform --run <run-id> --scope tb.apb
zddv --project my_project axi4lite-analyze axi4lite_trace.json
zddv --project my_project axi4-analyze axi4_trace.json
zddv --project my_project async-fifo-analyze async_fifo_cdc_trace.json
zddv --project my_project ucie-analyze ucie_trace.json
zddv --project my_project axi4-waveform --input axi4.vcd
zddv --project my_project axi4-waveform --run <run-id> --scope tb.axi
zddv --project my_project axi4lite-waveform --input axi4lite.vcd
zddv --project my_project axi4lite-waveform --run <run-id> --scope tb.axi
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
       ├── Questa     ← build/run + UCDB summary coverage implemented
       ├── VCS        ← build/run + native VDB/URG coverage implemented
       └── Xcelium    ← xrun build/run foundation implemented
```

The CLI and future GUI must use the same core APIs. Simulator-specific command construction stays inside simulator adapters.

## Repository Layout

```text
src/zddv/              Python package and CLI
src/zddv/simulator/    Simulator adapter layer
examples/counter/      Self-checking SystemVerilog counter example
examples/async_fifo/   Dual-clock Gray-pointer FIFO verification example
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
- [x] RTL async FIFO example with concurrent CDC stress and assertions
- [x] Async-FIFO CDC normalized-event invariant checker

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
- [x] APB normalized-trace transaction analysis
- [x] AXI4-Lite normalized-trace protocol analysis
- [x] AXI4 burst normalized-trace foundation
- [x] AXI4 burst VCD waveform extraction
- [x] AXI4 exclusive-access size/alignment, response, timing, and observed-pair checks
- [x] AXI4 AxCACHE/AxPROT/AxQOS/AxREGION width checks and AxREGION 4KB consistency
- [x] AXI4 reserved AxCACHE encoding checks and B/M/RA/WA attribute decoding
- [x] AXI4 optional USER-sideband capture, backpressure-stability checking, transaction evidence, and explicit interface-width validation
- [x] AXI4 data-width-aware AxSIZE and WSTRB byte-lane legality checks
- [ ] Exhaustive AXI4 optional-sideband/coherency-adjacent semantics
- [x] Async-FIFO CDC normalized-event invariant analysis
- [x] UCIe public 68B/256B FLIT trace and link-health foundation
- [ ] Specification-complete UCIe protocol/PHY conformance checking
- [x] Source/hierarchy index
- [x] Waveform-to-source cross-probing
- [x] Targeted VCD value-change probing
- [x] Simulator-independent UVM report/test metadata ingestion
- [x] UVM snapshot-to-run correlation
- [x] Phase/objection-aware UVM lifecycle trace normalization and SQLite persistence
- [x] Explicit `sequencer@@sequence` report-context evidence
- [x] Normalized sequence state lifecycle reconstruction from explicit JSON evidence
- [x] Normalized sequence-item handshake analysis with SQLite event/violation persistence and history queries
- [x] Portable SystemVerilog item instrumentation helper targeting the explicit `ZDDV_UVM_ITEM` marker contract
- [x] Portable SystemVerilog sequence instrumentation helper targeting the explicit `ZDDV_UVM_SEQUENCE` marker contract
- [x] Observed UVM grant order plus explicit ARB_REQUEST contention/bypass evidence
- [ ] Automatic sequence/item instrumentation adapters and vendor-policy-aware arbitration reconstruction

### APB Trace Analysis

ZDDV can reconstruct APB transactions from a simulator-independent JSON trace.
Each sample represents values observed on a PCLK edge. The analyzer validates the
setup/access sequence, tracks wait states, checks requester-signal stability through
the access phase, rejects active PSTRB on reads, and records PSLVERR on the completion
cycle.

```json
{
  "source": "uvm-apb-monitor",
  "samples": [
    {"cycle": 10, "PSEL": 1, "PENABLE": 0, "PWRITE": 1, "PADDR": "0x10", "PWDATA": "0x55"},
    {"cycle": 11, "PSEL": 1, "PENABLE": 1, "PREADY": 1, "PWRITE": 1, "PADDR": "0x10", "PWDATA": "0x55"}
  ]
}
```

Run:

```bash
zddv --project my_project apb-analyze apb_trace.json
```

The JSON report is written to `.zddv/protocols/apb/latest.json` by default and
contains reconstructed transactions, wait-state counts, error responses, and
cycle-localized protocol violations.

### AXI4-Lite Trace Analysis

`zddv axi4lite-analyze <trace.json>` reconstructs AXI4-Lite reads and writes
from clock-edge samples of the five independent channels: AW, W, B, AR, and R.
Write address and data handshakes are accepted independently and paired in
acceptance order. Read and write responses are correlated in order because
AXI4-Lite has no transaction IDs.

The analyzer checks VALID and payload stability while READY is LOW, reports
responses that precede their requests, identifies incomplete requests at trace
end, records SLVERR/DECERR responses, and rejects EXOKAY because AXI4-Lite does
not support exclusive responses. Multiple outstanding transactions are
supported and paired in acceptance order.

```bash
zddv --project my_project axi4lite-analyze axi4lite_trace.json
```

The default report is `.zddv/protocols/axi4lite/latest.json`. The repository
also contains `examples/axi4lite_trace.json` and CI exercises the CLI against it.

### AXI4 Burst Trace Analysis

`zddv axi4-analyze <trace.json>` extends protocol reconstruction to burst-aware
AXI4. It tracks AW/AR transaction IDs, AxLEN/AxSIZE/AxBURST, ordered write data
without WID, BID/RID response correlation, read-data interleaving across
different IDs, and WLAST/RLAST termination. A normalized trace can also provide
`address_width_bits` for the AXI `ADDR_WIDTH` property; values must be 1..64
bits and accepted AWADDR/ARADDR values are checked against that interface width.
It also checks WRAP burst geometry and the requirement that each AXI burst remain
inside one 4KB address region.

The analyzer also rejects reserved AXI4 AxCACHE encodings and decodes Bufferable, Modifiable, Read-Allocate, and Write-Allocate attributes into each reconstructed transaction.

Optional AWUSER, ARUSER, WUSER, RUSER, and BUSER values are preserved when
present and participate in the same VALID/READY payload-stability checks as the
rest of their channel payload. Their meaning remains implementation-defined, so
ZDDV never invents USER semantics. A normalized trace can opt into interface-width
checks with a `user_signal_widths` object keyed by those five signal names. Each
value is a non-negative bit width; zero declares the signal absent. When both request
widths are supplied, AWUSER and ARUSER must match the AXI `USER_REQ_WIDTH`
relationship. When WUSER, BUSER, and RUSER widths are all supplied, RUSER must equal
WUSER + BUSER, matching `USER_DATA_WIDTH + USER_RESP_WIDTH`. Partial metadata
remains partial and does not cause missing widths to be guessed. For every USER
signal with an explicit width greater than zero, the normalized analyzer now requires
that signal to be physically present in each sample where its channel VALID is
asserted, including stalled cycles; a zero-width declaration rejects any raw
observation of that signal. When the metadata is omitted, existing USER transport
behavior is unchanged.

ZDDV also reports the Arm USER configuration guidance as explicit non-failing
`advisories`: request widths above the 128-bit guidance maximum, response widths
above 16 bits, USER data widths above `DATA_WIDTH/2`, USER data widths that are
not an integer multiple of the data-bus width in bytes, and changing lower
RUSER response bits across a multi-beat read response. These findings never
change protocol `status` by themselves because the specification presents the
limits as guidance and the response-bit behavior as a recommendation.

`zddv axi4-waveform` records the declared widths of USER signals that are actually
present in the selected VCD scope and feeds that evidence into the same checks. A
USER signal missing from the VCD is not automatically treated as a zero-width
physical interface signal.

For exclusive accesses, the analyzer checks the 16-transfer and 128-byte limits,
power-of-two total byte count, total-size address alignment, completion of an
observed matching exclusive read before its write starts, matching observable
read/write attributes, and OKAY/EXOKAY response consistency. An unmatched
exclusive write returning OKAY remains a legal failed-exclusive outcome.

When a normalized trace provides `data_width_bits`, ZDDV accepts the standard
AXI data widths of 8/16/32/64/128/256/512/1024 bits, bounds AxSIZE by that
interface width, and checks each accepted WSTRB value against the byte lanes
permitted by the write beat's address and transfer size. Narrow and unaligned
writes are handled per beat, and any subset of valid lanes, including WSTRB=0,
is legal.

A trace can also provide `absent_master_signals` when interface metadata proves
that an optional AXI4 master-output signal is physically not implemented. ZDDV
then applies the AMBA AXI4 master-interface default for the declared signal
(AxID/AxREGION/AxLEN/AxBURST/AxLOCK/AxCACHE/AxQOS, width-derived AxSIZE, or
width-derived all-ones WSTRB). This is explicit interface evidence: ordinary
missing sample fields are not treated as absent signals, and a signal declared
absent is rejected if it appears anywhere in the trace. Width-derived defaults
require `data_width_bits`.

This is a normalized-trace foundation, not a claim of exhaustive AXI/ACE/AXI5
coverage. Topology-dependent cache reachability and optional coherency/domain/
snoop/MMU attributes remain outside the current model.

```bash
zddv --project my_project axi4-analyze axi4_trace.json
```

The default report is `.zddv/protocols/axi4/latest.json`.

ZDDV can decode full burst-aware AXI4 directly from a VCD waveform with
`axi4-waveform`. The extractor samples ACLK edges, auto-detects a complete AXI4
scope when unambiguous, preserves optional ID/sideband signals when present,
requires WDATA and RDATA to use the same standard AXI data width, verifies that
WSTRB has one bit per data byte, derives `data_width_bits`, and feeds the same
normalized burst analyzer used by `axi4-analyze`.

```bash
zddv --project my_project axi4-waveform --input axi4.vcd
zddv --project my_project axi4-waveform --run <run-id> --scope tb.axi
```

The normalized trace is written to
`.zddv/protocols/axi4/waveform-trace.json` and the analyzed report to
`.zddv/protocols/axi4/waveform-latest.json`. AW, W-beat, AR, R-beat, and
response timestamps are retained so protocol violations and reconstructed bursts
can be cross-referenced to the waveform. VCD extraction requires AWADDR and ARADDR
to expose the same 1..64-bit `ADDR_WIDTH` and records that width as
`address_width_bits`. It also records `ID_W_WIDTH`
from matching AWID/BID declarations and `ID_R_WIDTH` from matching ARID/RID
declarations when both signals in a pair are present with the same width. Missing
or partially dumped ID declarations remain unknown; VCD omission is never promoted
to the protocol-defined width-zero/physically-absent state. The normalized analyzer
accepts optional `id_widths` metadata and validates 0..32-bit bounds, explicit
width-zero signal absence, and observed ID values without inferring widths from
transaction data.

ZDDV can also decode APB directly from a VCD waveform. `apb-waveform` samples
signals on PCLK edges, emits the same normalized trace model, then runs the same
protocol checker. If exactly one waveform scope contains PCLK, PSEL, and PENABLE,
the scope is selected automatically; otherwise use `--scope`.

```bash
zddv --project my_project apb-waveform --input apb.vcd
zddv --project my_project apb-waveform --run <run-id> --scope tb.apb
```

The normalized extracted trace is written to
`.zddv/protocols/apb/waveform-trace.json` and the analyzed report to
`.zddv/protocols/apb/waveform-latest.json`. Waveform timestamps are preserved
alongside logical sample cycles and transaction start/end cycles.

ZDDV can decode AXI4-Lite directly from a VCD waveform. `axi4lite-waveform`
samples all five VALID/READY channels on ACLK edges, emits the normalized trace
model, and runs the same ordering, stability, response, and transaction checks.
Automatic scope detection requires one scope containing ACLK and all five channel
handshake pairs; use `--scope` when multiple buses are present.

```bash
zddv --project my_project axi4lite-waveform --input axi4lite.vcd
zddv --project my_project axi4lite-waveform --run <run-id> --scope tb.axi
```

The extracted trace is written to
`.zddv/protocols/axi4lite/waveform-trace.json` and the analyzed report to
`.zddv/protocols/axi4lite/waveform-latest.json`. AW, W, AR, and response
timestamps are preserved in reconstructed transactions.

### UCIe Public FLIT Trace Analysis

`zddv ucie-analyze <trace.json>` adds an intentionally conservative UCIe-oriented
foundation based only on public UCIe Consortium material. The normalized
`public-flit-68-256` profile records 68B/256B FLITs, a 2-byte ACK/NAK header
indication, explicit monitor CRC health, TX/RX direction, timestamps, and optional
negotiated link metadata.

```bash
zddv --project my_project ucie-analyze ucie_trace.json
```

The default report is `.zddv/protocols/ucie/latest.json`. Trace validity and link
health are separate: malformed normalized evidence returns FAIL, while observed NAKs
or CRC errors produce `health=DEGRADED` without claiming a protocol violation.

Optional `negotiated.spec_version` and `negotiated.data_rate_gt_s` fields add a
public-generation consistency check. ZDDV models 32 GT/s as the public maximum through
UCIe 2.0 and 64 GT/s for UCIe 3.0; the historical `frequency_gt_s` input remains
accepted and is aliased to `data_rate_gt_s`. This is deliberately a generation
ceiling check, not complete speed-negotiation or package-profile validation.

This is not a UCIe conformance checker. PHY behavior, training-state timing, retry
rules, protocol mappings, exact CRC construction, and other specification-only rules
remain outside this public foundation. Public references:
https://www.uciexpress.org/specifications,
https://www.uciexpress.org/post/introduction-to-ucie-webinar-q-a-recap, and
https://www.uciexpress.org/post/ucie-3-0-specification-redefining-chiplet-interconnects.

### Asynchronous FIFO / CDC Dynamic Invariant Analysis

`zddv async-fifo-analyze <trace.json>` checks a simulator-independent event trace
from the FIFO write and read clock domains. Each local-domain event carries the
request/block/accept decision plus binary and Gray pointer values before and after
that local clock event.

The checker verifies accepted-operation pointer increments (including modulo wrap),
pointer stability for blocked/idle operations, binary-to-Gray encoding, one-bit local
Gray transitions, full/empty blocking semantics, normalized reset zeroing, and event
trace continuity.

This is deliberately a **dynamic FIFO invariant checker**, not static CDC signoff.
A sampled trace cannot by itself prove synchronizer structure, metastability MTBF,
timing constraints, or physical CDC implementation. Those require later structural
and implementation-aware CDC analysis. The JSON report records this limitation as
`not_static_cdc_signoff: true`.

```bash
zddv --project my_project async-fifo-analyze examples/async_fifo_cdc_trace.json
```

The default report is `.zddv/cdc/async-fifo/latest.json`.


### Coverage-Hole Test Suggestions

After generating a normalized hole report, ZDDV can turn each explicit uncovered
point into a deterministic, reviewable test intent:

```bash
zddv --project my_project coverage-holes --show 20
zddv --project my_project coverage-suggest --show 20
```

The default output is `.zddv/coverage/test-suggestions.json`. Suggestions preserve
the hole type plus available RTL/FEC/FSM/toggle evidence. For example, an explicit
missing toggle direction can be named, and an FSM transition already present in the
coverage evidence can be used as the objective. ZDDV does not invent DUT behavior,
generate test code, or execute the suggested stimulus automatically.

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

### Debug Studio Source Index

`zddv index` writes `.zddv/design/index.json` with deterministic file metadata,
SHA-256 hashes, source design units, instance locations, duplicate-unit detection,
and the configured top hierarchy. `zddv hierarchy` renders the same normalized
hierarchy in the terminal.

The source-level index remains deterministic and simulator-independent. For
tool-resolved hierarchy, `zddv elaborate` asks the Verilator adapter to export its
elaborated parser tree and writes `.zddv/design/elaborated.json`. Verilator
5.022+ uses JSON parser output; older supported versions use the legacy XML
export path. `zddv hierarchy --elaborated` renders that normalized instance
hierarchy without changing the source-index contract.

### Debug Studio Waveform Index

`zddv waveform-index` selects the latest run with a waveform by default, or a
specific run with `--run`. It writes a normalized JSON catalog under
`.zddv/waveforms/` and refreshes `latest.json`.

For VCD, ZDDV indexes hierarchical scopes, signal paths, widths, identifier
codes, timescale, file size, and SHA-256 fingerprint while stopping at the VCD
declaration boundary rather than loading value-change samples. FST remains
metadata-only by default. When the user explicitly supplies `--fst2vcd` (optionally
with an executable path), ZDDV converts the FST to a temporary VCD, derives the
same normalized scope/signal catalog, records the adapter provenance, and removes
the temporary VCD after indexing. Converter failures and security rejections are
surfaced; ZDDV does not bypass them.

```bash
zddv --project my_project waveform-index --input trace.fst --fst2vcd
zddv --project my_project waveform-index --input trace.fst --fst2vcd /opt/gtkwave/bin/fst2vcd
```

### Targeted Waveform Value Probing

`zddv waveform-probe` streams only requested VCD signals from the value-change
section instead of loading the complete waveform. FST uses the same normalized
probe path only when `--fst2vcd` is explicitly supplied; the converted VCD is
temporary and the persisted artifact identity remains the original FST. A signal
can be selected by exact hierarchical path or by a unique leaf name; ambiguous
leaf names are rejected and must be disambiguated with the full path. Optional
inclusive `--start` / `--end` timestamps and `--max-changes` bounds keep debug
queries deterministic on large waveforms.

```bash
zddv --project my_project waveform-probe tb_top.dut.count --run <run-id>
zddv --project my_project waveform-probe count clk --input trace.vcd --start 100 --end 500
zddv --project my_project waveform-probe count --input trace.fst --fst2vcd --start 100 --end 500
```

Probe reports are written under `.zddv/waveforms/probes/` and retain the source
waveform fingerprint, timescale, normalized signal metadata, exact timestamps,
values, truncation status, and converter provenance when used. This complements
`crossprobe`, which maps waveform signals back to RTL source locations. Cross-probe
and assertion-correlation flows keep FST metadata-only by default and can opt in
explicitly with `--fst2vcd [PATH]`; temporary VCD output is not retained.

### Debug Studio Drivers/Loads Navigation

`zddv connectivity` writes a normalized source-level structural connectivity
index to `.zddv/design/connectivity.json`. Query a signal with:

```bash
zddv --project my_project connectivity count --unit counter
```

The index records driver/load evidence from simple continuous/procedural
assignments, module port boundaries, and named/positional connections to known
child design units. Every edge keeps its source file and line plus instance/port
metadata where applicable.

This is deliberately a conservative **source-level** view, not elaborated
connectivity. Generate choices, macros, binds, interface/modport semantics,
complex lvalues, and other constructs require later simulator-AST enrichment.

### Debug Studio Cross-Probing

`zddv crossprobe <signal>` correlates a waveform signal with the normalized
source hierarchy and searches the resolved SystemVerilog design unit for the
signal declaration. Full waveform paths, source-style suffix paths, and unique
short signal names are supported. Ambiguous short names are rejected so debug
navigation does not silently select the wrong signal. FST inputs remain metadata-only
unless `--fst2vcd [PATH]` is supplied explicitly; converter provenance is retained
in the cross-probe report while the original FST remains the artifact of record.

The report records the waveform signal, matched hierarchy path, RTL unit,
source declaration, source-level driver/load evidence, match type, and the
design/connectivity/waveform index artifacts used as evidence. When valid persisted
elaboration resolves the waveform scope, the source-structural connectivity is
qualified with that exact parent instance path; instance-port edges include an
exact child instance only when the elaborated hierarchy proves one unique match,
otherwise candidate ambiguity remains explicit. This does not relabel the evidence
as exact elaborated net connectivity. Source lookup is intentionally conservative:
when the scope matches but a declaration cannot be identified on a single source
line, ZDDV returns a partial result instead of claiming an exact source location.

### Assertion-to-Waveform Debug Correlation

`zddv assertion-waveform` joins stored assertion events to the exact simulation
run and its waveform index. The JSON report includes run/test/seed context,
waveform format and timescale, and conservative signal hints when assertion text
contains an exact waveform signal name or hierarchical path. Missing waveforms
remain visible as uncorrelated events rather than being silently dropped. FST-backed
runs remain metadata-only unless `--fst2vcd [PATH]` is supplied; when enabled,
converter provenance is recorded and one converted index is cached per run for the
correlation pass.

### Evidence-Ranked Root-Cause Candidates

`zddv root-cause --run <run-id>` composes existing run, assertion, waveform,
cross-probe, and source-connectivity evidence into a deterministic debug ranking.
When a failing assertion names an indexed waveform signal, ZDDV cross-probes that
signal into the RTL hierarchy and elevates explicit driver sites above declaration
or assertion-only anchors. Timed-out runs retain the explicit timeout status and
configured timeout as runtime evidence.

The `evidence_score` is deliberately not a causal probability. It is the sum of
documented evidence classes (for example an explicit driver edge, an exact waveform
signal hint, an RTL declaration match, and a failing assertion). Every score basis
and supporting artifact is retained in `.zddv/debug/root-cause.json`; ZDDV does
not claim that the top-ranked candidate caused the failure.

### Evidence-Backed Debug Probe Suggestions

`zddv debug-probes --run <run-id>` consumes the deterministic root-cause ranking
and emits only probes that are directly justified by retained failure evidence. If an
assertion is explicitly correlated to an indexed waveform signal, ZDDV emits a
reviewable `waveform-probe` command for that exact signal and run. Suggestions retain
the source candidate rank, evidence score, assertion/log coordinates, waveform
artifact, and any resolved RTL driver/declaration evidence.

When no waveform artifact or exact signal hint exists, the report emits explicit
blockers instead of inventing a signal or time window. Probe suggestions are written
to `.zddv/debug/probe-suggestions.json` and do not execute automatically.

### Provider-Neutral AI RCA Context

`zddv ai-rca-context --run <run-id>` packages the existing deterministic
root-cause ranking and evidence-backed debug-probe suggestions into
`.zddv/debug/ai-rca-context.json`. The bundle carries an SHA-256 digest of the
canonical evidence payload plus a downstream prompt contract that requires any
assistant to distinguish observations, hypotheses, unknowns, and proposed next
checks.

This command does **not** contact an AI provider, transmit project data, execute
commands, or reinterpret `evidence_score` as a causal probability. The generated
bundle is review-required before any external use.

### Phase 4 — Advanced Verification

- [x] Questa adapter foundation (build/run, VCD, assertions, run-linked UVM)
- [x] Questa native per-run UCDB coverage capture
- [x] Questa UCDB merge + summary normalization into ZDDV coverage history
- [x] Questa ordinary functional covergroup-bin normalization into ZDDV functional coverage
- [x] Questa complementary detailed code-coverage evidence retention (XML + zero-hit source detail)
- [x] Questa statement/branch plus scalar condition/expression FEC item/source normalization and coverage-hole reporting
- [x] Questa documented multibit-expression input-term-bit FEC plus FSM state/transition item normalization
- [x] Questa by-instance binary/extended toggle transition item-level normalization from native XML
- [ ] Questa multibit-condition and enumerated/unknown toggle item-level normalization pending verified native schemas
- [x] VCS execution adapter foundation
- [x] VCS native per-run coverage database capture
- [x] VCS multi-run URG merge/report evidence retention
- [x] VCS normalized URG dashboard score ingestion and percentage-native history snapshots
- [x] VCS documented global covergroup type/instance covered/expected count ingestion
- [x] VCS documented module-level line/branch covered/total count ingestion from `modinfo.txt`
- [x] VCS documented instance-level line/condition/toggle/branch aggregation plus separate FSM state/transition/sequence counts
- [x] VCS module-level line/condition/toggle/FSM-transition/branch covered/total count ingestion from `modinfo.txt`
- [x] Xcelium execution adapter foundation
- [x] Xcelium native per-run coverage database capture
- [x] Xcelium IMC multi-run merge/report evidence retention
- [x] Xcelium Overall Average/Covered metric normalization into percentage-native coverage history
- [x] Xcelium verified block/expression/toggle item-level coverage-hole normalization
- [ ] Xcelium FSM/functional item-level normalization
- [x] Formal adapter API
- [x] Counterexample/witness normalization from normalized JSON and native VCD traces
- [x] Deterministic evidence-ranked root-cause candidate triage
- [x] Evidence-backed coverage-hole test objectives with review-required output
- [x] Review-gated generated assertion/test staging and explicit SHA-confirmed apply
- [x] Provider-neutral AI RCA context bundle with deterministic evidence SHA-256 and no automatic external transmission
- [x] Review-gated AI-assisted root-cause analysis with strict evidence references and auditable provenance
- [x] Desktop Debug Studio read-only foundation with source/hierarchy navigation, persisted elaborated hierarchy, bounded waveform probing, and detailed assertion/formal/UVM evidence panes
- [x] Review-gated lint/build/run and recorded-input historical rerun with exact SHA-256 approval, project/source/run-evidence revalidation, current-backend rebuild, and no verbatim historical-command replay

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
- [Generated Verification Review Workflow](docs/generated-verification.md)
- [AI Provenance Chain Audit](docs/ai-chain-audit.md)
- [AI RCA Context](docs/ai-rca-context.md)
- [Desktop Debug Studio](docs/desktop-debug-studio.md)

## License

A license will be selected before the first public release.

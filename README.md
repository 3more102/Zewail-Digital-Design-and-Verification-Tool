# Zewail Digital Design and Verification Tool (ZDDV)

[![ZDDV CI](https://github.com/3more102/Zewail-Digital-Design-and-Verification-Tool/actions/workflows/ci.yml/badge.svg)](https://github.com/3more102/Zewail-Digital-Design-and-Verification-Tool/actions/workflows/ci.yml)

ZDDV is an open digital design and verification environment for RTL development, simulation orchestration, regression, coverage, waveform artifacts, assertion, protocol, UVM, formal, and future AI-assisted verification workflows.

> Status: **v0.6 Multi-Simulator UVM + Coverage Foundation — run-aware UVM phase/objection and sequence-item history, normalized sequence lifecycles, observed grant-order plus explicit contention-window reconstruction, Questa UCDB/code/functional coverage normalization, and Verilator/Questa/VCS execution with native VCS coverage plus normalized URG score/count history**

## What Works Today

- TOML-based verification projects
- RTL/testbench source discovery
- Simulator-adapter architecture
- Verilator detection and version reporting
- Questa/QuestaSim native `vlib`/`vlog`/`vsim` build/run foundation with version detection, seeds, plusargs, timeouts, VCD capture, assertion ingestion, and run-linked UVM normalization
- Synopsys VCS native `vcs` -> `simv` build/run foundation with version detection, UVM 1.2 compilation, deterministic seeds, plusargs, timeouts, VCD capture, assertion ingestion, run-linked UVM normalization, per-run native `.vdb` coverage capture, multi-run URG merge/report evidence, normalized dashboard scores, and documented global covergroup type/instance counts
- Questa per-run UCDB capture, multi-run `vcover merge`, normalized `vcover report -summary` metrics, ordinary covergroup-bin ingestion, complementary XML/zero-hit evidence, and normalized statement/branch source-linked `coverage-holes`; condition/expression/toggle/FSM item normalization remains pending
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
- Simulator-independent UVM report-log normalization with test-name discovery, severity summaries, source/report metadata, SQLite persistence, run correlation, and history CLI
- UVM phase/objection lifecycle normalization from standard `+UVM_PHASE_TRACE` / `+UVM_OBJECTION_TRACE` report evidence, plus conservative `sequencer@@sequence` report-context evidence, persisted in SQLite
- Simulator-independent UVM sequence **state** lifecycle JSON model with transition validation, nested parent/sequencer evidence, partial-trace handling, run correlation, SQLite snapshots, and history CLI
- Simulator-independent UVM sequence-item handshake normalization with run correlation, SQLite snapshot/event persistence, history filtering, and observed GRANT-order evidence
- Explicit UVM `WAIT_FOR_GRANT` / `GRANT` / `SEND_REQUEST` contention-window reconstruction with pending-request evidence; arbitration policy/fairness is not inferred
- Simulator-independent functional coverage snapshots and per-bin database
- APB normalized-trace transaction reconstruction with wait-state and protocol-violation analysis
- APB transaction extraction directly from VCD waveforms at configurable clock edges
- AXI4-Lite transaction extraction directly from VCD waveforms with five-channel handshake sampling
- AXI4-Lite normalized-trace reconstruction with independent channel handshake and backpressure checks
- AXI4 burst analysis with IDs, lengths/types, WLAST/RLAST, 4KB-boundary checks, core exclusive-access semantics, AxCACHE/AxPROT/AxQOS/AxREGION validation, optional AWUSER/WUSER/BUSER/ARUSER/RUSER transport evidence, and data-width-aware WSTRB byte-lane checks
- Asynchronous-FIFO CDC dynamic invariant analysis for local binary/Gray pointers and full/empty blocking behavior
- Burst-aware AXI4 transaction extraction directly from VCD waveforms with timestamp preservation
- Public-facts-based UCIe 68B/256B FLIT trace and link-health analysis with ACK/NAK and CRC summaries
- Compatibility path for packaged Verilator 5.020 coverage generation
- SQLite verification results database and run history
- Selective rerun of historical PASS / FAIL / TIMEOUT runs
- JUnit XML export for CI systems
- Failure-signature normalization and grouping across failing seeds
- CI on Python 3.11, 3.12, and 3.13
- End-to-end Verilator CI examples, including a dual-clock asynchronous FIFO
- SystemVerilog assertions enabled in Verilator build/lint flows
- Deterministic source index with file hashes and source locations
- Source-level module/interface hierarchy with recursive-cycle protection
- VCD waveform scope/signal index with FST artifact metadata support
- Targeted VCD signal value probing with exact/unique-name resolution, time windows, and bounded change capture
- Assertion-to-waveform run correlation with conservative signal hints
- Source-level structural drivers/loads navigation with assignment and instance-port evidence
- Waveform-to-RTL source cross-probing with hierarchy-aware signal resolution

## Quick Start

Requirements:

- Python 3.11+
- Verilator available in `PATH` for the default backend
- Optional Questa/QuestaSim: `vlib`, `vlog`, `vsim`, and `vcover` available in `PATH` for native UCDB coverage workflows
- Optional Synopsys VCS: `vcs` available in `PATH`

Install ZDDV for development:

```bash
git clone https://github.com/3more102/Zewail-Digital-Design-and-Verification-Tool.git
cd Zewail-Digital-Design-and-Verification-Tool
python -m pip install -e ".[dev]"
zddv doctor
zddv doctor --simulator questa
zddv doctor --simulator vcs
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
```

For a Questa project, setting `coverage = true` in `[run]` enables native
coverage instrumentation and writes `coverage.ucdb` inside each run directory.
`zddv coverage` then merges run UCDBs with `vcover merge`, runs
`vcover report -summary`, and stores normalized aggregate metrics in the same
SQLite coverage-history model. It also runs `vcover report -cvg -details` and,
when ordinary covergroup bins are present, ingests them into ZDDV's existing
functional-coverage database. `zddv fcov-history` and `zddv fcov-holes` can then
inspect those normalized bins. ZDDV also retains complementary detailed code-coverage
evidence: XML output for machine-readable follow-up plus a `-zeros -details`
report for zero-hit source/file-line evidence. ZDDV also normalizes documented
statement/branch rows from `vcover report -details -code sb`, allowing
`zddv coverage-holes` to report source-linked statement and branch misses.
Condition/expression/toggle/FSM item normalization remains pending. Questa's
weighted total coverage remains a separate simulator-reported value.

For a VCS project with `coverage = true`, ZDDV instruments compilation and simulation with `-cm line+cond+fsm+tgl+branch` and directs each run to its own `coverage.vdb` using `-cm_dir`. The per-run database is recorded only when it actually exists. `zddv coverage` then uses Synopsys URG to merge all per-run VDBs into `.zddv/coverage/coverage.vdb` and retain an `urg-report` directory. When `dashboard.txt` contains the documented Total Coverage Summary, ZDDV normalizes the overall SCORE plus available LINE/COND/TOGGLE/FSM/BRANCH/ASSERT/GROUP percentages and stores them in a percentage-native SQLite history. If the documented Total Groups Coverage Summary is present, ZDDV also stores its global covergroup type and instance COVERED/EXPECTED counts without converting percentages into synthetic counts. Code-metric object-count aggregation remains pending; if the dashboard is absent or unparseable, the merged VDB/report evidence remains available without a numeric snapshot.

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
zddv --project my_project connectivity
zddv --project my_project connectivity count --unit counter
zddv --project my_project waveform-index
zddv --project my_project waveform-index --run <run-id>
zddv --project my_project waveform-index --input trace.vcd
zddv --project my_project waveform-probe tb_top.dut.count --start 0 --end 1000
zddv --project my_project crossprobe tb_top.dut.count
zddv --project my_project crossprobe tb_top.dut.count --input trace.vcd
zddv --project my_project assertion-waveform --status FAIL
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
zddv --project my_project coverage
zddv --project my_project coverage-history --limit 20
zddv --project my_project coverage-holes --show 20
zddv --project my_project coverage-holes --type line --output .zddv/coverage/line-holes.json
zddv --project my_project assertions --limit 100
zddv --project my_project assertions --status FAIL
zddv --project my_project uvm-analyze uvm.log --source questa
zddv --project my_project uvm-analyze --run <run-id>
zddv --project my_project uvm-history --run <run-id>
zddv --project my_project uvm-item-analyze item_trace.json
zddv --project my_project uvm-item-analyze item_trace.json --run <run-id>
zddv --project my_project uvm-item-history --limit 20
zddv --project my_project uvm-arbitration-analyze arbitration_trace.json
zddv --project my_project uvm-sequence-analyze sequence_trace.json
zddv --project my_project uvm-sequence-analyze sequence_trace.json --run <run-id>
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
       └── Xcelium    ← planned
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
- [x] AXI4 optional USER-sideband capture, backpressure-stability checking, and transaction evidence
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
- [x] Normalized sequence-item handshake analysis with SQLite persistence/history
- [x] Observed UVM item GRANT-order reconstruction
- [x] Explicit UVM WAIT_FOR_GRANT/GRANT contention-window reconstruction
- [ ] Automatic sequence state/arbitration instrumentation and simulator adapters

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
different IDs, and WLAST/RLAST termination. It also checks WRAP burst geometry
and the requirement that each AXI burst remain inside one 4KB address region.

The analyzer also rejects reserved AXI4 AxCACHE encodings and decodes Bufferable, Modifiable, Read-Allocate, and Write-Allocate attributes into each reconstructed transaction.

Optional AWUSER, ARUSER, WUSER, RUSER, and BUSER values are preserved when
present and participate in the same VALID/READY payload-stability checks as the
rest of their channel payload. Their meaning and width are implementation-defined,
so ZDDV does not invent USER semantic or width legality without explicit interface
metadata.

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
can be cross-referenced to the waveform.

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

This is not a UCIe conformance checker. PHY behavior, training-state timing, retry
rules, protocol mappings, exact CRC construction, and other specification-only rules
remain outside this public foundation. Public references:
https://www.uciexpress.org/specifications and
https://www.uciexpress.org/post/introduction-to-ucie-webinar-q-a-recap.

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

This v0.4 foundation is intentionally a **source-level** index. It does not claim
to replace elaboration: generate-time choices, parameter specialization, binds,
and tool-resolved hierarchy will be enriched later through simulator-adapter AST
data while preserving the same normalized ZDDV model.

### Debug Studio Waveform Index

`zddv waveform-index` selects the latest run with a waveform by default, or a
specific run with `--run`. It writes a normalized JSON catalog under
`.zddv/waveforms/` and refreshes `latest.json`.

For VCD, ZDDV indexes hierarchical scopes, signal paths, widths, identifier
codes, timescale, file size, and SHA-256 fingerprint while stopping at the VCD
declaration boundary rather than loading value-change samples. FST is currently
recorded as metadata-only until a converter or simulator-native adapter is added.

### Targeted VCD Value Probing

`zddv waveform-probe` streams only requested VCD signals from the value-change
section instead of loading the complete waveform. A signal can be selected by exact
hierarchical path or by a unique leaf name; ambiguous leaf names are rejected and
must be disambiguated with the full path. Optional inclusive `--start` / `--end`
timestamps and `--max-changes` bounds keep debug queries deterministic on large
waveforms.

```bash
zddv --project my_project waveform-probe tb_top.dut.count --run <run-id>
zddv --project my_project waveform-probe count clk --input trace.vcd --start 100 --end 500
```

Probe reports are written under `.zddv/waveforms/probes/` and retain the waveform
timescale, normalized signal metadata, exact timestamps, values, and truncation
status. This complements `crossprobe`, which maps waveform signals back to RTL
source locations.

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
navigation does not silently select the wrong signal.

The report records the waveform signal, matched hierarchy path, RTL unit,
source declaration, source-level driver/load evidence, match type, and the
design/connectivity/waveform index artifacts used as evidence. Source lookup is
intentionally conservative: when the scope matches but a declaration cannot be
identified on a single source line, ZDDV returns a partial result instead of
claiming an exact source location.

### Assertion-to-Waveform Debug Correlation

`zddv assertion-waveform` joins stored assertion events to the exact simulation
run and its waveform index. The JSON report includes run/test/seed context,
waveform format and timescale, and conservative signal hints when assertion text
contains an exact waveform signal name or hierarchical path. Missing waveforms
remain visible as uncorrelated events rather than being silently dropped.

### Phase 4 — Advanced Verification

- [x] Questa adapter foundation (build/run, VCD, assertions, run-linked UVM)
- [x] Questa native per-run UCDB coverage capture
- [x] Questa UCDB merge + summary normalization into ZDDV coverage history
- [x] Questa ordinary functional covergroup-bin normalization into ZDDV functional coverage
- [x] Questa complementary detailed code-coverage evidence retention (XML + zero-hit source detail)
- [x] Questa statement/branch item/source normalization and coverage-hole reporting
- [ ] Questa condition/expression/toggle/FSM item-level normalization
- [x] VCS execution adapter foundation
- [x] VCS native per-run coverage database capture
- [x] VCS multi-run URG merge/report evidence retention
- [x] VCS normalized URG dashboard score ingestion and percentage-native history snapshots
- [x] VCS documented global covergroup type/instance covered/expected count ingestion
- [ ] VCS code-metric covered/total object-count ingestion from module/instance detail reports
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

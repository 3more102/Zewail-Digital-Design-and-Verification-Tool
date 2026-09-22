# UVM run-level marker adapter

ZDDV supports explicit machine-readable UVM evidence embedded in simulator logs:

- `ZDDV_UVM_SEQUENCE {json}` for sequence lifecycle events.
- `ZDDV_UVM_ITEM {json}` for sequence-item handshake events.

The run-level adapter scans one simulation log, detects which marker families are
present, and delegates them to the existing sequence and item analyzers. It does
not infer lifecycle or arbitration facts from ordinary simulator/UVM prose.

## CLI

```bash
zddv --project my_project uvm-marker-analyze simulation.log
zddv --project my_project uvm-marker-analyze --run <run-id>
```

The combined report is written to `.zddv/uvm/markers/latest.json` by default.
Child sequence/item snapshots keep their normal SQLite persistence and run
correlation.

## Automatic post-run ingestion

Questa, VCS, and Xcelium invoke the run-level adapter automatically when their
captured output contains a `ZDDV_UVM_` marker. Ordinary UVM report analysis is
triggered only by actual `UVM_INFO`, `UVM_WARNING`, `UVM_ERROR`, or
`UVM_FATAL` report text, so marker-only logs do not create empty report
snapshots.

Malformed explicit markers are recorded as marker-analysis failures without
changing the already recorded simulator run status. This keeps simulation
execution evidence separate from evidence-adapter validity.

## Evidence boundary

This adapter is opt-in and evidence-first. A testbench or helper must emit the
explicit markers. ZDDV does not guess hidden UVM sequence states, waiting queues,
priority, lock state, or arbitration policy from vendor transcript text.

# UVM marker log adapter

ZDDV can extract machine-readable sequence lifecycle and sequence-item handshake
evidence directly from a simulator log. This removes the manual JSON-file step
while preserving the same simulator-independent analyzers and SQLite history.

## Marker contract

Each marker is one log line. The marker token is followed by one JSON object.

```text
ZDDV_UVM_SEQUENCE {"sequence_id":"seq-7","sequence":"smoke_seq","sequencer":"uvm_test_top.env.sqr","state":"UVM_BODY","time":"120ns"}
ZDDV_UVM_ITEM {"item_id":"item-3","event":"GRANT","sequence_id":"seq-7","sequence":"smoke_seq","sequencer":"uvm_test_top.env.sqr","item":"req","transaction_id":3,"time":"130ns"}
```

Simulator prefixes such as Questa's leading `#` are allowed because ZDDV locates
the marker token inside the line. The JSON object must otherwise follow the
existing normalized contracts documented in `docs/uvm-sequence-trace.md` and
`docs/uvm-item-trace.md`.

Run against a log directly:

```bash
zddv --project my_project uvm-marker-analyze simulation.log
```

Or use an existing ZDDV run:

```bash
zddv --project my_project uvm-marker-analyze --run <run-id>
```

For run-linked analysis, extracted sequence and item snapshots retain the run ID,
simulator result, and return code through the existing persistence layers.

## Automatic simulator ingestion

The Questa and VCS adapters invoke the marker adapter automatically when a run log
contains a `ZDDV_UVM_` marker. Ordinary UVM report normalization remains
independent and continues to run when standard `UVM_` report text is present.

## Evidence boundary

The adapter does not infer hidden UVM behavior from vendor log prose. It consumes
only explicit marker JSON and then delegates lifecycle, identity, ordering, and
observed-grant validation to the established UVM sequence/item analyzers.

This is an adapter foundation, not automatic testbench instrumentation. A UVM
environment must still emit the markers at the sequence/item observation points.
A later layer can provide reusable UVM-side instrumentation without changing this
log contract.

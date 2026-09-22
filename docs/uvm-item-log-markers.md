# UVM Sequence-Item Log Markers

ZDDV can extract explicit, simulator-independent sequence-item evidence directly from ordinary simulation logs and route it through the existing UVM item analyzer and SQLite history.

## Command

```text
zddv --project <project> uvm-item-log-analyze <simulation.log>
```

When the log belongs to a recorded ZDDV run, the path can be omitted:

```text
zddv --project <project> uvm-item-log-analyze --run <run-id>
```

The default marker token is `ZDDV_UVM_ITEM`. Each marker must be followed by one JSON object on the same log line:

```text
ZDDV_UVM_ITEM {"item_id":"item-17","event":"GRANT","sequence_id":"seq-3","sequencer":"uvm_test_top.env.seqr","transaction_id":17,"time":"120 ns"}
ZDDV_UVM_ITEM {"item_id":"item-17","event":"REQUEST","sequence_id":"seq-3","sequencer":"uvm_test_top.env.seqr","transaction_id":17,"time":"120 ns"}
ZDDV_UVM_ITEM {"item_id":"item-17","event":"ITEM_DONE","sequence_id":"seq-3","sequencer":"uvm_test_top.env.seqr","transaction_id":17,"time":"128 ns"}
```

The marker may be embedded inside a normal simulator or `UVM_INFO` line. ZDDV searches the full line for the marker token, parses only the JSON object after it, and records the original 1-based log line in event metadata.

## Evidence model

The marker vocabulary remains the existing item-handshake contract:

- `GRANT`: explicit evidence that the sequence was granted.
- `REQUEST`: explicit evidence that the request item was sent to the sequencer/driver path.
- `ITEM_DONE`: explicit driver-completion evidence.
- `RESPONSE`: optional response evidence.

This mapping follows the Accellera UVM sequence API ordering: `wait_for_grant` returns after the sequencer grants the sequence, `send_request` is called after that grant, and `wait_for_item_done` waits for the driver's `item_done` or `put` completion path.

References:

- Accellera UVM 1.2 Class Reference, sequence API: https://www.accellera.org/images/downloads/standards/uvm/UVM_Class_Reference_Manual_1.2.pdf
- Accellera UVM standards downloads: https://www.accellera.org/downloads/standards/uvm

## Failure behavior

The adapter is intentionally evidence-first:

- malformed marker JSON is an error and reports the source log line;
- marker payloads must be JSON objects;
- zero marker events is an error rather than a synthetic PASS;
- event legality, ordering, identity consistency, partial traces, and observed grant order are delegated to the existing UVM item analyzer;
- no vendor-specific report text is guessed or reinterpreted.

The extracted portable trace is written to `.zddv/uvm/items/extracted/latest.json` by default. The normalized analysis remains under `.zddv/uvm/items/` and is persisted to the existing UVM item SQLite tables.

## Boundary

This adapter does not inject UVM callbacks, modify the UVM library, infer hidden sequencer queues, infer arbitration mode, or claim fairness from grant order. Automatic UVM-side instrumentation and vendor-specific adapters remain separate follow-on work.

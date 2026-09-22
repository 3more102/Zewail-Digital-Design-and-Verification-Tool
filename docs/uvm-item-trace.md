# UVM Sequence-Item Handshake Trace

ZDDV can analyze explicit, simulator-independent sequence-item handshake evidence with:

```text
zddv --project <project> uvm-item-analyze <trace.json>
```

Each analysis is also persisted in the project SQLite database. Query recent snapshots with:

```text
zddv --project <project> uvm-item-history
zddv --project <project> uvm-item-history --status FAIL
zddv --project <project> uvm-item-history --run <run-id>
```

The normalized event vocabulary is:

- `GRANT` — sequencer arbitration granted the sequence/item path.
- `REQUEST` — the sequence sent the item request to the sequencer.
- `ITEM_DONE` — the driver completed the request through the UVM item-done/put completion path.
- `RESPONSE` — optional response evidence associated with the item.

This follows the public Accellera UVM sequence-item API model: arbitration grant precedes the request in a complete trace, while driver completion is observed through the item-done/put path. Responses are optional and are not required for an item to be classified as completed.

## JSON contract

```json
{
  "source": "instrumentation-name",
  "events": [
    {
      "item_id": "item-17",
      "event": "GRANT",
      "sequence_id": "seq-3",
      "sequence": "axi_write_seq",
      "sequencer": "uvm_test_top.env.seqr",
      "item": "axi_item",
      "transaction_id": 17,
      "time": "120 ns",
      "metadata": {}
    }
  ]
}
```

`item_id` and `event` are required. Sequence, sequencer, item name, transaction ID, time, and metadata are optional evidence.

## Explicit log-marker adapter

ZDDV can ingest the same normalized event evidence directly from a simulator or UVM log when the testbench emits an explicit marker:

```text
ZDDV_UVM_ITEM {"item_id":"item-17","event":"GRANT","sequence_id":"seq-3","sequence":"axi_write_seq","sequencer":"uvm_test_top.env.seqr","item":"axi_item","transaction_id":17,"time":"120 ns"}
```

Everything after `ZDDV_UVM_ITEM` on that line must be one JSON object using the same event fields as the standalone trace format. Other log lines are ignored. ZDDV records the source log line in event metadata and does not reinterpret ordinary simulator or UVM messages as handshake evidence.

```text
zddv --project <project> uvm-item-log-analyze simulation.log
zddv --project <project> uvm-item-log-analyze --run <run-id>
```

When `--run` is supplied without a path, ZDDV reads the simulation log stored for that run and keeps the run status, return code, and simulator as separate correlation evidence.


## Partial traces

A trace may begin at `REQUEST`, `ITEM_DONE`, or `RESPONSE`. ZDDV marks that item as partial rather than inventing a failure for evidence that may have been captured after the handshake had already started.

Once earlier evidence is present, backward ordering is a violation. Examples include `ITEM_DONE` after an observed `GRANT` but before `REQUEST`, or a late `GRANT` after `REQUEST`.

## Persistence and current boundary

ZDDV stores each normalized item-handshake snapshot in `.zddv/results.db`, including summary counters, optional run correlation, and the normalized per-event evidence. The JSON snapshot under `.zddv/uvm/items/snapshots/` remains the complete portable artifact.

This layer validates event ordering, duplicate events, stable item identity, persisted history, and explicit marker ingestion from logs. It does not infer vendor log formats, reconstruct arbitration priority/fairness, validate delta-cycle timing, or compare transaction payloads.

Reference basis: Accellera UVM 1.2 User Guide and UVM 1.2 Class Reference for the sequence/sequencer request-grant and driver item-done/put API flow.

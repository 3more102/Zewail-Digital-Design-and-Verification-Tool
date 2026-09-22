# UVM Sequence-Item Handshake Trace

ZDDV can analyze explicit, simulator-independent sequence-item handshake evidence with:

```text
zddv --project <project> uvm-item-analyze <trace.json>
```

ZDDV can also consume explicit item evidence directly from ordinary UVM report logs when instrumentation emits the dedicated `[ZDDV_ITEM]` report ID:

```text
zddv --project <project> uvm-item-log <simulation.log>
zddv --project <project> uvm-item-log --run <run-id>
```

A report payload is a shell-like list of `key=value` tokens. `event` and `item_id` are required; `sequence_id`, `sequence`, `sequencer`, `item`, `transaction_id`, and `time` are optional. Unknown keys are preserved as event metadata, and values containing spaces may be quoted. When `time` is omitted, the timestamp from the UVM report line is retained.

Example:

```text
UVM_INFO @ 10 ns: uvm_test_top.env.seqr@@axi_write_seq [ZDDV_ITEM] event=GRANT item_id=item-17 sequence_id=seq-3 sequence=axi_write_seq sequencer=uvm_test_top.env.seqr item=axi_item transaction_id=17
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

## Partial traces

A trace may begin at `REQUEST`, `ITEM_DONE`, or `RESPONSE`. ZDDV marks that item as partial rather than inventing a failure for evidence that may have been captured after the handshake had already started.

Once earlier evidence is present, backward ordering is a violation. Examples include `ITEM_DONE` after an observed `GRANT` but before `REQUEST`, or a late `GRANT` after `REQUEST`.

## Persistence and current boundary

ZDDV stores each normalized item-handshake snapshot in `.zddv/results.db`, including summary counters, optional run correlation, and the normalized per-event evidence. The JSON snapshot under `.zddv/uvm/items/snapshots/` remains the complete portable artifact.

This layer validates event ordering, duplicate events, and stable item identity. The `ZDDV_ITEM` adapter is explicit instrumentation: ZDDV does not guess vendor-private log formats. Automatic interception of UVM sequence/sequencer calls, arbitration priority/fairness reconstruction, delta-cycle timing validation, and transaction-payload comparison remain outside this layer.

Reference basis: Accellera UVM 1.2 User Guide and UVM 1.2 Class Reference for the sequence/sequencer request-grant and driver item-done/put API flow.

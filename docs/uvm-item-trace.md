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

- `ARB_REQUEST` — optional evidence that an item/sequence entered sequencer arbitration and began waiting for a grant.
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

## Arbitration and fairness evidence

When `ARB_REQUEST` events are present, ZDDV reconstructs the pending request set per sequencer and reports matched grants, contended grants, maximum pending depth, per-request bypass counts, and per-sequence grant share. Existing traces that begin at `GRANT` remain valid; ZDDV does not invent waiting-time evidence that was never captured.

An optional user policy can bound the number of competing grants allowed while a request waits:

```text
zddv --project <project> uvm-item-analyze <trace.json> --max-bypass 3
```

Exceeding that explicit bound emits `ARBITRATION_BYPASS_LIMIT`. Without a supplied bound, grant-share and bypass values are evidence only and are not treated as a universal fairness verdict.

## Persistence and current boundary

ZDDV stores each normalized item-handshake snapshot in `.zddv/results.db`, including summary counters, optional run correlation, and the normalized per-event evidence. The JSON snapshot under `.zddv/uvm/items/snapshots/` remains the complete portable artifact.

This layer validates event ordering, duplicate events, stable item identity, and explicit arbitration request/grant evidence. It does not infer vendor log formats or the sequencer's configured arbitration policy, and it does not validate delta-cycle timing or compare transaction payloads.

Reference basis: Accellera UVM 1.2 User Guide and UVM 1.2 Class Reference for the sequence/sequencer request-grant and driver item-done/put API flow.

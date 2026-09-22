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
zddv --project <project> uvm-item-violations <snapshot-id>
zddv --project <project> uvm-item-violations <snapshot-id> --code LATE_GRANT --item item-17
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

## Explicit log-marker adapter

The same evidence can be emitted inside a simulator/UVM log with an explicit marker:

```text
ZDDV_UVM_ITEM {"item_id":"item-17","event":"GRANT","sequence_id":"seq-3","sequence":"axi_write_seq","sequencer":"uvm_test_top.env.seqr","item":"axi_item","transaction_id":17,"time":"120 ns"}
```

Everything after `ZDDV_UVM_ITEM` on that line must be one JSON object using the standalone trace fields. Other log lines are ignored. ZDDV records the source log line in event metadata and does not reinterpret ordinary simulator or UVM messages as item-handshake evidence.

```text
zddv --project <project> uvm-item-log-analyze simulation.log
zddv --project <project> uvm-item-log-analyze --run <run-id>
```

When `--run` is supplied without a path, ZDDV reads the simulation log recorded for that run and retains run status, return code, and simulator as correlation evidence.


## Portable SystemVerilog instrumentation helper

Generate an opt-in helper that emits the same `ZDDV_UVM_ITEM {json}` contract:

```text
zddv --project <project> uvm-item-instrument
```

By default ZDDV writes `tb/zddv_uvm_item_trace_pkg.sv` and inserts that exact file at the front of the project's testbench source list so the package is compiled before ordinary testbench sources. Use `--no-add-source` when source ordering is managed externally, and `--force` to replace an existing generated helper.

The generated package has no dependency on `uvm_pkg`. Call its tasks explicitly from verification code that owns the corresponding semantic point. Use `zddv_uvm_item_grant` after a grant has been obtained, `zddv_uvm_item_request` when the request is submitted, `zddv_uvm_item_done` when driver completion is observed, and `zddv_uvm_item_response` only when explicit response evidence exists. The same stable `item_id` should be used across events for one item. Optional sequence, sequencer, item-name, and transaction-ID fields may be left empty when that context is unavailable.

This helper is explicit instrumentation, not a UVM-library patch or zero-touch hook. It does not infer hidden sequencer queues, arbitration mode, priority, fairness, or timing behavior.


## Partial traces

A trace may begin at `REQUEST`, `ITEM_DONE`, or `RESPONSE`. ZDDV marks that item as partial rather than inventing a failure for evidence that may have been captured after the handshake had already started.

Once earlier evidence is present, backward ordering is a violation. Examples include `ITEM_DONE` after an observed `GRANT` but before `REQUEST`, or a late `GRANT` after `REQUEST`.

## Observed arbitration evidence

For every explicit `GRANT`, ZDDV preserves the trace-order grant index and groups grants by sequencer. The report includes observed sequence IDs, adjacent sequence switches, and the longest contiguous known sequence streak. A partial trace with no `GRANT` does not create synthetic arbitration evidence.

This is an observed grant-order reconstruction only. ZDDV does not infer the sequencer arbitration mode, request waiting queue, priority, lock state, or fairness from grant order alone.

When explicit `ARB_REQUEST` events are present, ZDDV additionally reconstructs the observed pending-request set per sequencer. It reports matched grants, grants without request evidence, contended grants, maximum pending depth, requests still pending at trace end, per-request bypass counts, and per-sequence request/grant statistics. Existing GRANT-only traces remain valid and do not gain synthetic waiting evidence.

A user may optionally impose a verification bound on observed bypasses:

```text
zddv --project <project> uvm-item-analyze <trace.json> --max-bypass 3
```

A request that remains pending while more than the configured number of competing grants are observed produces `ARBITRATION_BYPASS_LIMIT`. This threshold is an explicit user policy; ZDDV does not treat any bypass count as a universal UVM fairness rule.

## Persistence and current boundary

ZDDV stores each normalized item-handshake snapshot in `.zddv/results.db`, including summary counters, optional run correlation, normalized per-event evidence, and every detected violation with its code, source event index, item ID, event type, and message. `uvm-item-violations` queries that failure evidence without reopening the JSON artifact. The JSON snapshot under `.zddv/uvm/items/snapshots/` remains the complete portable artifact.

This layer validates event ordering, duplicate events, stable item identity, explicit marker ingestion, reconstructs observed grant order from explicit GRANT evidence, and reconstructs waiting/contended request evidence only when ARB_REQUEST is explicitly provided. It does not infer vendor log formats, the configured arbitration mode/priority/lock policy, delta-cycle timing, or transaction payload equality.

Reference basis: Accellera UVM 1.2 User Guide and UVM 1.2 Class Reference for the sequence/sequencer request-grant and driver item-done/put API flow.

The generated helper exposes `zddv_uvm_item_arb_request(...)` for the optional explicit `ARB_REQUEST` event accepted by the current item analyzer. Emit it when a request becomes eligible/waiting on its sequencer. ZDDV uses this evidence to reconstruct observed contention and bypass counts; it does not infer a universal UVM fairness rule.

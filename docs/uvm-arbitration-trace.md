# UVM Sequence-Item Arbitration Trace

ZDDV can reconstruct observed sequence-item arbitration from an explicit, simulator-independent JSON event trace:

```bash
zddv --project <project> uvm-arbitration-analyze <trace.json>
```

The normalized event model follows the documented UVM sequence-item handshake:

- `WAIT_FOR_GRANT` — the sequence has issued a request to the sequencer and is waiting for arbitration.
- `GRANT` — the sequencer has granted that request.
- `SEND_REQUEST` — the sequence sends the item after the grant.
- `ITEM_DONE` — driver completion evidence for the request.
- `RESPONSE` — optional response evidence.

Accellera's UVM class reference states that `wait_for_grant` issues a request to the current sequencer; after it returns, the sequence must call `send_request`, and `send_request` may only be called after `wait_for_grant`. The sequencer supports multiple arbitration modes, including FIFO, weighted, random, strict-priority variants, and user arbitration. Because policy, relevance, locks/grabs, and vendor instrumentation can affect which request is selected, ZDDV reconstructs only contention that is explicitly visible in the normalized trace and does not infer fairness or policy correctness.

Reference:
- Accellera UVM downloads: https://www.accellera.org/downloads/standards/uvm
- UVM 1.2 Class Reference: https://www.accellera.org/images/downloads/standards/uvm/UVM_Class_Reference_Manual_1.2.pdf

## JSON contract

Each event requires:

- `request_id`: stable identifier for one arbitration/request lifecycle.
- `event`: one of `WAIT_FOR_GRANT`, `GRANT`, `SEND_REQUEST`, `ITEM_DONE`, `RESPONSE`.
- `sequence_id`: stable sequence instance identifier.
- `sequencer`: sequencer instance path/name.

Optional evidence:

- `sequence`
- `item_id`
- `transaction_id`
- `priority`
- `lock_request`
- `time`
- `metadata`

The top-level `arbitration_mode` is optional and retained as evidence. ZDDV does not use it to claim that the observed grant order is correct because a complete policy decision may also depend on relevance, lock/grab state, or user arbitration.

Example:

```json
{
  "source": "zddv-uvm-helper",
  "arbitration_mode": "UVM_SEQ_ARB_FIFO",
  "events": [
    {
      "request_id": "req-a",
      "event": "WAIT_FOR_GRANT",
      "sequence_id": "seq-a",
      "sequence": "read_seq",
      "sequencer": "uvm_test_top.env.agent.seqr",
      "priority": 100,
      "time": "10 ns"
    },
    {
      "request_id": "req-b",
      "event": "WAIT_FOR_GRANT",
      "sequence_id": "seq-b",
      "sequence": "write_seq",
      "sequencer": "uvm_test_top.env.agent.seqr",
      "priority": 100,
      "time": "10 ns"
    },
    {
      "request_id": "req-b",
      "event": "GRANT",
      "sequence_id": "seq-b",
      "sequencer": "uvm_test_top.env.agent.seqr",
      "time": "11 ns"
    }
  ]
}
```

At each observed `GRANT`, ZDDV snapshots the requests currently waiting on the same sequencer. This produces a `grant_windows` record with the observed contenders, contender count, request/grant event distance, sequence identity, priority evidence, and lock-request evidence.

## Checks

The foundation reports deterministic violations for:

- duplicate lifecycle events,
- request identity changes,
- `SEND_REQUEST` observed before an available `GRANT`,
- `ITEM_DONE` observed before `SEND_REQUEST`,
- `RESPONSE` observed before `SEND_REQUEST`,
- a grant for a request that had already left the observed pending set.

A trace that starts after `WAIT_FOR_GRANT` is retained as partial evidence rather than failed solely for missing earlier instrumentation.

## Output

The default report is:

```text
.zddv/uvm/arbitration/latest.json
```

Immutable JSON snapshots are also written under:

```text
.zddv/uvm/arbitration/snapshots/
```

The summary includes request/event/violation counts, pending requests, granted-but-unsent requests, active requests, partial traces, grant-window count, contended-grant count, and maximum observed contenders.

## Limitations

This is an explicit-evidence reconstruction foundation, not automatic UVM instrumentation and not a proof of sequencer fairness. It does not infer hidden requests from simulator logs, validate user-defined arbitration, prove relevance filtering, reconstruct lock/grab ownership, or prove the required no-delay/delta-cycle relationship from free-form time strings. SQLite persistence/history is intentionally deferred to a later increment.

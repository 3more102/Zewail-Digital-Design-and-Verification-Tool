# UVM Sequencer Arbitration Round Trace

ZDDV can analyze explicit, simulator-independent sequencer arbitration-round evidence with:

```text
zddv --project <project> uvm-arbitration-analyze <trace.json>
```

This layer complements the sequence-item handshake analyzer. The handshake report can preserve observed GRANT order, while the arbitration-round report accepts explicit contender sets and the selected winner for each arbitration decision.

## JSON contract

```json
{
  "source": "instrumentation-name",
  "mode": "UVM_SEQ_ARB_STRICT_FIFO",
  "fairness_max_wait_rounds": 4,
  "rounds": [
    {
      "round_id": "arb-17",
      "sequencer": "uvm_test_top.env.seqr",
      "winner_sequence_id": "seq-b",
      "time": "120 ns",
      "contenders": [
        {
          "sequence_id": "seq-a",
          "sequence": "background_seq",
          "priority": 100,
          "request_order": 0
        },
        {
          "sequence_id": "seq-b",
          "sequence": "urgent_seq",
          "priority": 300,
          "request_order": 1
        }
      ],
      "metadata": {}
    }
  ]
}
```

`round_id`, `sequencer`, `winner_sequence_id`, and a non-empty `contenders` array are required for every round. Each contender requires `sequence_id`; `sequence`, `priority`, `request_order`, and `metadata` are optional evidence.

The accepted UVM arbitration modes are:

- `UVM_SEQ_ARB_FIFO`
- `UVM_SEQ_ARB_WEIGHTED`
- `UVM_SEQ_ARB_RANDOM`
- `UVM_SEQ_ARB_STRICT_FIFO`
- `UVM_SEQ_ARB_STRICT_RANDOM`
- `UVM_SEQ_ARB_USER`

A local `UNSPECIFIED` mode is also accepted when the trace producer does not know the sequencer mode.

## Deterministic checks

ZDDV validates structural evidence for every mode: duplicate round IDs, duplicate contenders, winners that are not contenders, and inconsistent sequence names.

Policy checks are intentionally evidence-gated:

- FIFO ordering is checked only when every contender in that round has an explicit `request_order`.
- Strict arbitration checks the selected winner against the highest explicit priority only when every contender has a priority.
- Strict-FIFO additionally checks request order among the highest-priority contenders when that order is explicit.
- Random, weighted-random, and user-defined selections are retained as evidence but are not declared correct or incorrect from a single winner alone.

This matches the public UVM 1.2 arbitration model: FIFO chooses in request order; strict modes first restrict selection to the highest-priority requests, with FIFO or random selection among that set.

## Fairness evidence

For each `sequencer + sequence_id`, the report records eligible rounds, grants, misses, grant rate, current consecutive wait, and maximum observed consecutive wait.

A sequence accumulates wait only in rounds where it is explicitly present in the contender set. If it is absent from the next round on that sequencer, the consecutive-wait streak resets.

`fairness_max_wait_rounds` is optional. Without it, wait metrics are descriptive only. When configured, exceeding the bound produces `FAIRNESS_WAIT_EXCEEDED`. ZDDV does not invent a universal fairness bound because UVM arbitration modes and user-defined sequencers do not imply one common finite bound.

## Artifacts and current boundary

The default report is written to:

```text
.zddv/uvm/arbitration/latest.json
```

A timestamped portable snapshot is also retained under:

```text
.zddv/uvm/arbitration/snapshots/
```

An optional `--run <run-id>` correlates the arbitration report with an existing ZDDV simulation run.

This foundation does not infer vendor log formats, hidden sequencer request queues, lock/grab state, or random/weighted statistical correctness. SQLite persistence/history and automatic simulator instrumentation remain future work.

Reference basis: Accellera UVM 1.2 Class Reference and UVM 1.2 User Guide arbitration semantics.

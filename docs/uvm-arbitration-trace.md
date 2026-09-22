# UVM Sequencer Arbitration Evidence

ZDDV analyzes explicit, simulator-independent UVM sequencer arbitration evidence with:

```text
zddv --project <project> uvm-arbitration-analyze <trace.json>
zddv --project <project> uvm-arbitration-history --limit 20
```

The supported normalized modes are the six UVM sequence arbitration modes:

- `UVM_SEQ_ARB_FIFO`
- `UVM_SEQ_ARB_WEIGHTED`
- `UVM_SEQ_ARB_RANDOM`
- `UVM_SEQ_ARB_STRICT_FIFO`
- `UVM_SEQ_ARB_STRICT_RANDOM`
- `UVM_SEQ_ARB_USER`

## JSON contract

```json
{
  "source": "instrumentation-name",
  "mode": "UVM_SEQ_ARB_STRICT_FIFO",
  "rounds": [
    {
      "round_id": "arb-1",
      "sequencer": "uvm_test_top.env.seqr",
      "winner_sequence_id": "seq-b",
      "time": "120 ns",
      "metadata": {},
      "candidates": [
        {
          "sequence_id": "seq-a",
          "sequence": "read_seq",
          "request_order": 1,
          "priority": 100,
          "metadata": {}
        },
        {
          "sequence_id": "seq-b",
          "sequence": "write_seq",
          "request_order": 2,
          "priority": 200,
          "metadata": {}
        }
      ]
    }
  ]
}
```

The producer must provide the already-eligible candidate set for each arbitration round.
That means lock/grab blocking and `is_relevant()` filtering are not reconstructed by
ZDDV from incomplete logs.

## Deterministic checks

For `UVM_SEQ_ARB_FIFO`, every candidate needs a unique `request_order`, and the
earliest eligible request must win.

For `UVM_SEQ_ARB_STRICT_FIFO`, every candidate needs `priority` plus unique
`request_order`; the winner must have the highest priority, with FIFO order breaking
ties at that priority.

For `UVM_SEQ_ARB_STRICT_RANDOM`, every candidate needs `priority`, and the winner
must belong to the highest-priority candidate set. ZDDV does not attempt to validate
which member of that set a random draw should select.

`UVM_SEQ_ARB_RANDOM`, `UVM_SEQ_ARB_WEIGHTED`, and `UVM_SEQ_ARB_USER` retain the
winner and candidate set as evidence, but one observed result is not classified as
statistically fair or unfair.

## Wait evidence

For each `sequencer + sequence_id`, ZDDV records:

- eligible arbitration rounds;
- observed wins;
- current consecutive eligible losses;
- maximum consecutive eligible losses.

These are observed wait-round metrics, not a fairness verdict.

## Persistence

Every analysis writes an immutable JSON snapshot under
`.zddv/uvm/arbitration/snapshots/`, updates
`.zddv/uvm/arbitration/latest.json`, and persists snapshot/candidate rows in
`.zddv/results.db`. Optional `--run <run-id>` correlation links the snapshot to an
existing ZDDV simulation run.

## Scope boundary

This layer does not infer vendor logs, reproduce random-number-generator outcomes,
reconstruct user-defined arbitration functions, or claim starvation/fairness from a
finite trace. Automatic simulator instrumentation remains a later adapter layer.

Reference basis:

- Accellera/Verification Academy UVM 1.2 `uvm_sequencer_base::set_arbitration` documentation.
- Accellera UVM 1.2 reference implementation arbitration selection logic.

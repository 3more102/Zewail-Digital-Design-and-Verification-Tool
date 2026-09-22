# UVM Arbitration Evidence Trace

ZDDV can analyze explicit sequencer-arbitration evidence independently of simulator vendor logs:

```text
zddv --project <project> uvm-arb-analyze <trace.json>
zddv --project <project> uvm-arb-analyze <trace.json> --max-bypass 8
```

This trace is separate from `uvm-item-analyze`. Here, `REQUEST` means a request has entered the sequencer arbitration queue, and `GRANT` means that request was selected by the sequencer.

## JSON contract

Top-level fields:

- `mode`: one of `UVM_SEQ_ARB_FIFO`, `UVM_SEQ_ARB_WEIGHTED`, `UVM_SEQ_ARB_RANDOM`, `UVM_SEQ_ARB_STRICT_FIFO`, `UVM_SEQ_ARB_STRICT_RANDOM`, or `UVM_SEQ_ARB_USER`. If omitted, ZDDV uses the UVM default, `UVM_SEQ_ARB_FIFO`.
- `source`: optional evidence-source label.
- `max_bypass`: optional ZDDV fairness-policy threshold. This is not a UVM-standard guarantee.
- `events`: ordered arbitration evidence.

Each event has:

- `request_id`: unique request identity.
- `event`: `REQUEST` or `GRANT`.
- `sequence_id`: required on `REQUEST`; optional but identity-checked on `GRANT`.
- `sequence`, `sequencer`, `time`: optional evidence.
- `priority`: optional integer; defaults to 100, matching the UVM sequence default priority.

## Checks

ZDDV performs only constraints that can be justified from the selected arbitration mode:

- `UVM_SEQ_ARB_FIFO`: grants must follow request FIFO order.
- `UVM_SEQ_ARB_STRICT_FIFO`: the grant must select the highest-priority pending request; equal-priority requests are FIFO.
- `UVM_SEQ_ARB_STRICT_RANDOM`: the grant must come from the highest-priority pending set, but tie selection is not predicted.
- `UVM_SEQ_ARB_RANDOM`, `UVM_SEQ_ARB_WEIGHTED`, and `UVM_SEQ_ARB_USER`: the selected request is retained as observational evidence; ZDDV does not invent a deterministic winner.

For all modes, duplicate requests/grants, grants without an earlier request, and identity/priority changes are reported.

The optional `max_bypass` policy counts how many other grants occur while a request remains pending. Exceeding the user-supplied limit reports `FAIRNESS_BYPASS_LIMIT`. A finite trace with a pending request is not treated as proof of starvation by itself.

## Current boundary

This foundation consumes explicit normalized arbitration evidence and writes JSON snapshots under `.zddv/uvm/arbitration/`. SQLite persistence, vendor-log inference, automatic UVM instrumentation, and statistical fairness analysis for randomized/weighted modes remain planned.

Reference basis: Accellera UVM 1.2 Class Reference arbitration-mode definitions and UVM 1.2 User Guide sequence scheduling/priorities.

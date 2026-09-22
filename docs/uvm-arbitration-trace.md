# UVM arbitration and fairness trace

ZDDV can analyze explicit, simulator-independent arbitration evidence with:

```text
zddv --project <project> uvm-arbitration-analyze <trace.json>
zddv --project <project> uvm-arbitration-history --limit 20
```

The input contains an ordered `decisions` array. Each decision names the sequencer, the granted request, and the contender set visible for that arbitration decision. Request identity is carried by `request_id`, `sequence_id`, `sequence`, and optional `item_id` / integer `priority`.

## Checks

ZDDV reports violations when:

- a decision ID is reused;
- the same request appears more than once in one contender set;
- the granted request is not one of the contenders;
- a stable request changes sequence/item/priority/sequencer identity;
- an already granted request reappears or is granted again;
- an explicit project-defined fairness bound is exceeded.

## Fairness bound

`fairness_bound` is optional and may be supplied in the JSON or overridden by `--fairness-bound`. It is the maximum number of **observed arbitration decisions that a request may lose** while it is present as a contender. A request granted after two earlier losses therefore has `lost_decisions=2`.

When no bound is supplied, ZDDV still reports exposure counts, losing-decision counts, grant counts by sequence, pending requests, and the maximum observed wait, but it does not invent a fairness failure threshold.

This bound is deliberately project-defined. It is not presented as an Accellera UVM arbitration-policy default or as a guarantee of FIFO, random, weighted, or user arbitration behavior.

## Persistence

Each analysis writes the complete normalized JSON evidence under `.zddv/uvm/arbitration/` and stores summary history plus per-request fairness state in the project SQLite database. `uvm-arbitration-history` supports status and run-ID filtering.

## Current boundary

This layer does not infer vendor simulator log formats, infer the configured UVM arbitration mode, validate weighted/random probabilities, prove policy-specific priority ordering, validate delta-cycle timing, or compare transaction payloads. Those require stronger evidence than the normalized decision trace alone supplies.

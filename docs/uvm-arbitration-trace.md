# UVM arbitration and fairness trace

ZDDV can analyze explicit, simulator-independent arbitration evidence with:

```text
zddv --project <project> uvm-arbitration-analyze <trace.json>
zddv --project <project> uvm-arbitration-history --limit 20
```

The input contains an ordered `decisions` array. Each decision names the sequencer, the granted request, and the contender set visible for that arbitration decision. Request identity is carried by `request_id`, `sequence_id`, `sequence`, and optional `item_id` / integer `priority`. A top-level `mode` may declare the UVM arbitration mode for the trace, and an individual decision may override it. If omitted, the mode is `UNSPECIFIED`. A contender may also carry an explicit non-negative `request_order` supplied by the trace producer.

## Checks

ZDDV always reports structural violations when:

- a decision ID is reused;
- the same request appears more than once in one contender set;
- the granted request is not one of the contenders;
- a stable request changes sequence/item/priority/sequencer identity;
- an already granted request reappears or is granted again;
- an explicit project-defined fairness bound is exceeded.

When the trace explicitly declares a UVM arbitration mode, ZDDV applies only checks justified by the available evidence:

- `UVM_SEQ_ARB_FIFO`: checked only when every contender has `request_order`; the grant must select an earliest request.
- `UVM_SEQ_ARB_STRICT_FIFO`: highest-priority eligibility is checked when every contender has `priority`; FIFO tie-breaking is checked only when the highest-priority contenders also have `request_order`.
- `UVM_SEQ_ARB_STRICT_RANDOM`: highest-priority eligibility is checked when every contender has `priority`; the random winner among equal highest-priority contenders is not predicted.
- `UVM_SEQ_ARB_RANDOM`, `UVM_SEQ_ARB_WEIGHTED`, `UVM_SEQ_ARB_USER`, and `UNSPECIFIED`: the selected grant is retained as observational evidence and no deterministic winner is invented.

Each normalized decision gets a `policy_checks` entry with `CHECKED`, `PARTIAL`, `OBSERVATIONAL`, or `SKIPPED` status, plus the evidence used and any deterministic expected-request set.

## Fairness bound

`fairness_bound` is optional and may be supplied in the JSON or overridden by `--fairness-bound`. It is the maximum number of **observed arbitration decisions that a request may lose** while it is present as a contender. A request granted after two earlier losses therefore has `lost_decisions=2`.

When no bound is supplied, ZDDV still reports exposure counts, losing-decision counts, grant counts by sequence, pending requests, and the maximum observed wait, but it does not invent a fairness failure threshold.

This bound is deliberately project-defined. It is not presented as an Accellera UVM arbitration-policy default or as a guarantee of FIFO, random, weighted, or user arbitration behavior.

## Persistence

Each analysis writes the complete normalized JSON evidence under `.zddv/uvm/arbitration/` and stores summary history plus per-request fairness state in the project SQLite database. `uvm-arbitration-history` supports status and run-ID filtering.

## Current boundary

This layer does not infer vendor simulator log formats, hidden sequencer queues, lock/grab state, the configured arbitration mode, weighted/random probabilities, delta-cycle timing, or transaction payload semantics. Policy checks run only when the trace producer explicitly supplies the mode and the priority/request-order evidence required for that check.

Reference basis: Accellera UVM 1.2 Class Reference `uvm_sequencer_base::set_arbitration` and the UVM 1.2 User Guide sequence scheduling/priority sections.

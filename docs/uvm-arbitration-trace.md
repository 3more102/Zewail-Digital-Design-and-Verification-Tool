# UVM arbitration and fairness trace

ZDDV can analyze explicit, simulator-independent arbitration evidence with:

```text
zddv --project <project> uvm-arbitration-analyze <trace.json>
zddv --project <project> uvm-arbitration-history --limit 20
```

The input contains an ordered `decisions` array. Each decision names the sequencer, the granted request, and the contender set visible for that arbitration decision. Request identity is carried by `request_id`, `sequence_id`, `sequence`, and optional `item_id` / integer `priority`.

An optional top-level `mode` (or per-decision `mode` override) may name one of the six UVM arbitration modes: `UVM_SEQ_ARB_FIFO`, `UVM_SEQ_ARB_WEIGHTED`, `UVM_SEQ_ARB_RANDOM`, `UVM_SEQ_ARB_STRICT_FIFO`, `UVM_SEQ_ARB_STRICT_RANDOM`, or `UVM_SEQ_ARB_USER`. If mode is omitted, ZDDV records it as `UNSPECIFIED` and performs no policy-specific winner check.

## Checks

ZDDV reports violations when:

- a decision ID is reused;
- the same request appears more than once in one contender set;
- the granted request is not one of the contenders;
- a stable request changes sequence/item/priority/sequencer identity;
- an already granted request reappears or is granted again;
- an explicit project-defined fairness bound is exceeded;
- an explicitly configured FIFO/strict arbitration rule is contradicted by sufficient contender evidence.

## Evidence-gated UVM policy checks

Policy validation is opt-in through explicit `mode` evidence. ZDDV does not infer the configured sequencer mode from winner order.

- `UVM_SEQ_ARB_FIFO`: checked only when every contender in that decision carries a unique non-negative `request_order`; the earliest request must win.
- `UVM_SEQ_ARB_STRICT_FIFO`: checked only when every contender carries `priority`; the winner must be in the highest-priority set. FIFO tie-breaking is additionally checked when every highest-priority contender carries a unique `request_order`.
- `UVM_SEQ_ARB_STRICT_RANDOM`: checked only when every contender carries `priority`; the winner must be in the highest-priority set, but the random tie winner is not predicted.
- `UVM_SEQ_ARB_RANDOM`, `UVM_SEQ_ARB_WEIGHTED`, and `UVM_SEQ_ARB_USER`: winner choice remains observational. A finite trace does not prove random/weighted probability behavior or a user-defined arbitration function.

Missing priority or request-order evidence skips the corresponding deterministic check rather than producing a failure. Duplicate request-order values are reported as ambiguous when that order is needed for a FIFO check.

## Item evidence bridge

Explicit item request/grant evidence can be bridged into the same arbitration analyzer:

```text
zddv --project <project> uvm-arbitration-analyze items.json --item-trace --fairness-bound 2
zddv --project <project> uvm-arbitration-analyze simulation.log --item-log --fairness-bound 2
```

For each explicit `GRANT`, pending `ARB_REQUEST` events on the same sequencer form the contender set. The granted item becomes `granted_request_id`, and the resulting decisions are passed through the normal arbitration/fairness and policy checker; no second fairness implementation is used.

A decision is not emitted if any pending contender on that sequencer lacks `sequence_id` or `sequence`. This prevents incomplete evidence from being silently dropped from the contender set. The bridge preserves item/log provenance and the original input path. Optional `priority`, `request_order`, and `arbitration_mode` are propagated only when they are explicitly instrumented in event metadata; none are inferred.

## Fairness bound

`fairness_bound` is optional and may be supplied in the JSON or overridden by `--fairness-bound`. It is the maximum number of **observed arbitration decisions that a request may lose** while it is present as a contender. A request granted after two earlier losses therefore has `lost_decisions=2`.

When no bound is supplied, ZDDV still reports exposure counts, losing-decision counts, grant counts by sequence, pending requests, and the maximum observed wait, but it does not invent a fairness failure threshold.

This bound is deliberately project-defined. It is not presented as an Accellera UVM arbitration-policy default or as a guarantee of FIFO, random, weighted, or user arbitration behavior.

## Persistence

Each analysis writes the complete normalized JSON evidence under `.zddv/uvm/arbitration/` and stores summary history plus per-request fairness state in the project SQLite database. `uvm-arbitration-history` supports status and run-ID filtering.

## Current boundary

This layer does not infer vendor simulator log formats, hidden sequencer queues, lock/grab state, weighted/random probability distributions, user-defined arbitration behavior, delta-cycle timing, or transaction payloads. Policy-specific checks run only when the trace explicitly supplies the mode and the evidence required by that rule.

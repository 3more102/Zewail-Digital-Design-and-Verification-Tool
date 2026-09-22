# UVM Sequence Lifecycle Trace

ZDDV uses an explicit simulator-independent JSON contract for UVM sequence lifecycle
evidence. This avoids guessing sequence activity from vendor-specific transcript text.

The normalized state names follow the UVM sequence states defined by Accellera:

- `UVM_CREATED`
- `UVM_PRE_START`
- `UVM_PRE_BODY`
- `UVM_BODY`
- `UVM_ENDED`
- `UVM_POST_BODY`
- `UVM_POST_START`
- `UVM_STOPPED`
- `UVM_FINISHED`

Reference implementation:
`accellera-official/uvm-core/src/base/uvm_object_globals.svh` and
`src/seq/uvm_sequence_base.svh`.

## Input Schema

Top-level JSON:

```json
{
  "source": "my-uvm-sequence-monitor",
  "events": []
}
```

Each event contains:

| Field | Required | Meaning |
| --- | --- | --- |
| `sequence_id` | yes | Stable identity for one sequence instance |
| `sequence` | yes | Sequence type or instance label |
| `state` | yes | One of the normalized UVM sequence states |
| `sequencer` | no | Full sequencer path |
| `parent_sequence_id` | no | Parent sequence identity for nested/virtual sequences |
| `time` | no | Simulator time preserved as text |
| `metadata` | no | Adapter-specific JSON object retained without interpretation |

Example:

```json
{
  "source": "uvm-monitor",
  "events": [
    {
      "sequence_id": "seq-17",
      "sequence": "axi_write_seq",
      "sequencer": "uvm_test_top.env.axi_agent.seqr",
      "state": "UVM_PRE_START",
      "time": "10 ns"
    },
    {
      "sequence_id": "seq-17",
      "sequence": "axi_write_seq",
      "sequencer": "uvm_test_top.env.axi_agent.seqr",
      "state": "UVM_BODY",
      "time": "11 ns"
    }
  ]
}
```

## Explicit Log-Marker Adapter

ZDDV can also consume opt-in lifecycle markers embedded in simulator/UVM logs. The marker is:

```text
ZDDV_UVM_SEQUENCE {"sequence_id":"seq-17","sequence":"axi_write_seq","sequencer":"uvm_test_top.env.axi_agent.seqr","state":"UVM_BODY","time":"11 ns"}
```

Everything after `ZDDV_UVM_SEQUENCE` on that line must be one JSON object using the same fields as the standalone lifecycle trace. Other log lines are ignored. ZDDV records the source log line in event metadata and does not reinterpret ordinary simulator or UVM messages as sequence-state evidence.

The adapter API reuses the same lifecycle validator and SQLite persistence path:

```python
from zddv.uvm_sequence import analyze_uvm_sequence_log

result = analyze_uvm_sequence_log(project, "simulation.log")
result = analyze_uvm_sequence_log(project, None, run_id="run-id")
```

When a recorded run is supplied without a path, the adapter reads that run's simulation log and preserves run status, return code, and simulator correlation.

## Lifecycle Rules

For a normally completed sequence with pre/post callbacks enabled, ZDDV accepts:

```text
UVM_CREATED
  -> UVM_PRE_START
  -> UVM_PRE_BODY
  -> UVM_BODY
  -> UVM_ENDED
  -> UVM_POST_BODY
  -> UVM_POST_START
  -> UVM_FINISHED
```

`UVM_PRE_BODY` and `UVM_POST_BODY` are optional because UVM
`start(..., call_pre_post=0)` skips them. `UVM_STOPPED` is accepted as a terminal
state for a killed sequence.

A trace may begin after `UVM_CREATED` or end before a terminal state. Such partial
evidence is retained as an active/incomplete sequence and is not classified as a
verification failure by itself.

ZDDV reports violations for contradictory evidence such as backward/impossible state
transitions, consecutive duplicate states, events after `UVM_FINISHED` or
`UVM_STOPPED`, self-parenting, or identity changes for one `sequence_id`.

## Commands

```bash
zddv --project my_project uvm-sequence-analyze sequence_trace.json
zddv --project my_project uvm-sequence-analyze sequence_trace.json --run <run-id>
zddv --project my_project uvm-sequence-log-analyze simulation.log
zddv --project my_project uvm-sequence-log-analyze --run <run-id>
zddv --project my_project uvm-sequence-history --limit 20
```

The normalized report is written to
`.zddv/uvm/sequences/latest.json` by default. Immutable snapshot reports and event
rows are also persisted in the project SQLite database.

## Scope Boundary

This layer models sequence lifecycle evidence only. It accepts explicit `ZDDV_UVM_SEQUENCE`
JSON markers but does not infer sequence activity from ordinary UVM text logs, and it does not reconstruct sequencer arbitration,
request/grant timing, sequence-item payloads, driver completion, or transaction-level
semantics.

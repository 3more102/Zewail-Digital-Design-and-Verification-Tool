# Automatic UVM Sequence/Item Instrumentation Adapter

ZDDV can generate an opt-in UVM base-sequence adapter that emits the existing
`ZDDV_UVM_SEQUENCE` and `ZDDV_UVM_ITEM` marker contracts automatically for the
standard sequence/item path.

Generate it with:

```text
zddv --project <project> uvm-auto-instrument
```

The command creates and orders three testbench sources:

1. `tb/zddv_uvm_sequence_trace_pkg.sv`
2. `tb/zddv_uvm_item_trace_pkg.sv`
3. `tb/zddv_uvm_auto_trace_pkg.sv`

The first two are the existing simulator-independent marker helpers. The third
contains `zddv_instrumented_sequence #(REQ, RSP)`.

## Adoption

A sequence opts in by extending the generated base class and implementing
`zddv_body()` rather than overriding `body()`:

```systemverilog
class smoke_seq extends zddv_instrumented_sequence #(my_item);
  `uvm_object_utils(smoke_seq)

  function new(string name = "smoke_seq");
    super.new(name);
  endfunction

  virtual task zddv_body();
    my_item req;
    req = my_item::type_id::create("req");
    start_item(req);
    assert(req.randomize());
    finish_item(req);
  endtask
endclass
```

The adapter leaves test selection and execution unchanged. It does not start a
sequence or simulation automatically.

## Standard UVM hooks used

The adapter is based on public virtual APIs in the Accellera UVM reference
implementation for IEEE 1800.2-2020:

- `uvm_sequence_base::start`
- `pre_start`, `pre_body`, `body`, `post_body`, and `post_start`
- `uvm_sequence_base::start_item`
- `uvm_sequence_base::finish_item`
- `uvm_sequence::get_response`
- `uvm_object::get_inst_id`
- `uvm_sequence_base::get_sequence_state`

The adapter deliberately does **not** use `get_sequence_id()` as its trace
identity. The UVM reference implementation documents that method as internal
and not intended for user identification. ZDDV instead creates one trace ID per
`start()` execution from the public object instance ID plus an execution
serial.

## Automatically emitted evidence

For a normally completed sequence, the adapter can emit:

```text
UVM_CREATED
UVM_PRE_START
UVM_PRE_BODY
UVM_BODY
UVM_ENDED
UVM_POST_BODY
UVM_POST_START
UVM_FINISHED
```

A killed sequence is reported as `UVM_STOPPED` when `start()` returns in
that state. `UVM_PRE_BODY` and `UVM_POST_BODY` remain naturally absent when
UVM executes the sequence with `call_pre_post=0`.

For the inherited item API:

```text
start_item(item)
  -> ARB_REQUEST before the UVM call
  -> GRANT after start_item() returns

finish_item(item)
  -> REQUEST before the UVM call
  -> ITEM_DONE after finish_item() returns

get_response(rsp)
  -> RESPONSE after UVM returns a response
```

`finish_item()` in UVM contains the standard send/wait-for-done path, so ZDDV
does not claim a more precise internal timestamp than those public API
boundaries. Response markers reuse the request item ID when an explicit
transaction ID matches a previously completed request.

## Source ordering and overwrite rules

With the default source registration, ZDDV places the sequence marker package,
item marker package, then adapter package before existing testbench sources.
`--no-add-source` writes the files without changing `zddv.toml` source
ordering.

Existing marker helpers are reused and never overwritten implicitly. Before reuse,
ZDDV checks that each helper still exposes the expected package and emitter API; an
incompatible existing helper is rejected instead of being silently accepted. An
existing adapter is replaced only with `--force`. The adapter output is also rejected
if it collides with either marker-helper path.

## Evidence boundary

This is automatic instrumentation **after explicit adoption**, not a zero-touch
UVM-library patch. The following remain explicit/manual:

- sequences that override `body()` instead of implementing `zddv_body()`;
- derived overrides of `start`, `pre_start`, `pre_body`, `post_body`, or
  `post_start` that do not call `super`;
- direct `wait_for_grant` / `send_request` / `wait_for_item_done` flows;
- response delivery handled only through `response_handler()`;
- arbitration contender sets, priorities, request order, lock/grab state, and
  hidden sequencer queues;
- vendor-specific transcript text.

Arbitration-policy analysis remains evidence-gated. ZDDV checks deterministic
FIFO/strict-priority rules only when normalized arbitration evidence explicitly
contains the UVM mode and the priority/request-order fields required for that
rule. Random, weighted, and user-defined winner choice is not predicted.

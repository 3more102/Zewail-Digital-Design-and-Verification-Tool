# Portable UVM Sequence-Item Instrumentation

ZDDV can generate a simulator-independent UVM sequencer wrapper that emits explicit
`ZDDV_UVM_ITEM` JSON markers for the existing item-handshake analyzer.

## Generate the wrapper

```text
zddv --project <project> uvm-item-instrument
```

The default output is:

```text
.zddv/uvm/instrumentation/zddv_uvm_item_instrumentation.svh
```

A project-local path can be selected with `--output`.

## Integrate it

Compile or include the generated source after UVM is available, import its package, and
derive the project's ordinary sequencer from the generated base:

```systemverilog
`include "zddv_uvm_item_instrumentation.svh"

import zddv_uvm_item_instrumentation_pkg::*;

class axi_sequencer extends zddv_instrumented_sequencer #(axi_item);
  `uvm_component_utils(axi_sequencer)

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction
endclass
```

The generated class does not modify the UVM library and does not parse simulator-specific
text. A project that already has a custom sequencer must move the compatible custom
behavior onto this base (SystemVerilog has single class inheritance); ZDDV does not
monkey-patch an arbitrary existing sequencer hierarchy.

## Evidence emitted

The wrapper uses UVM sequencer API hooks and emits the existing normalized vocabulary:

- `GRANT`: captured when `wait_for_grant` returns. The marker is emitted later, when
  `send_request` provides stable item identity, but its `time` field preserves the
  captured grant time.
- `REQUEST`: emitted after `super.send_request`, so the UVM-assigned sequence and
  transaction IDs are available.
- `ITEM_DONE`: emitted for the normal `item_done` completion path. UVM 1.2 `get`
  completes the request by calling `item_done`, so that path is covered through the
  same completion hook.
- `RESPONSE`: emitted for `item_done(response)` and `put_response(response)`.
  UVM 1.2 `put(response)` delegates to `put_response`, so it is covered as well.

Request identity is retained for completion and response events. Response type and
response instance ID are kept in event metadata rather than replacing the request's
stable item identity.

## Analyze a generated-marker log

```text
zddv --project <project> uvm-item-log-analyze simulation.log
zddv --project <project> uvm-item-log-analyze --run <run-id>
```

The existing analyzer then validates ordering and identity, reconstructs explicit observed
grant order, writes JSON evidence, correlates the optional run, and persists the snapshot
in SQLite.

## Boundaries

- The generator targets the public UVM 1.2 sequencer request/grant and driver-sequencer
  completion/response flow.
- Response traffic sent only through a separate driver analysis port such as
  `rsp_port.write()` bypasses the sequencer pull interface and is not emitted by this
  wrapper.
- The wrapper records explicit API evidence; it does not infer arbitration policy,
  fairness, hidden waiting queues, or vendor log semantics.
- The repository CI validates generator output and command behavior. It does not claim
  compilation against a commercial simulator/UVM installation.

Reference behavior: UVM 1.2 defines `wait_for_grant` followed by `send_request`,
`item_done` as request completion, `get` as an implicit completion path, and
`put` as delegating the response to `put_response`.

# Formal Adapter API Foundation

## Purpose

The v0.7 formal foundation separates engine execution from result normalization
in the same way ZDDV separates simulator backends from shared verification evidence.

This increment defines two contracts:

1. `FormalBackend` for future engine adapters.
2. A simulator-independent JSON result model for assertion and cover evidence.

No formal engine is claimed as supported by this foundation alone.

## Execution Contract

A formal backend implements:

- `version()` — identify the engine version.
- `run(project, request)` — execute an engine-specific formal job and return command/log/result artifact metadata.
- `normalize(project, result)` — translate engine output into the shared result payload accepted by `normalize_formal_data()`.

`FormalRunRequest` can carry an optional property allow-list, bounded depth, and timeout.
The base contract does not prescribe an engine command line.

## Normalized Result Payload

```json
{
  "engine": "example-formal",
  "engine_version": "1.0",
  "source": "adapter-name",
  "properties": [
    {
      "name": "p_no_overflow",
      "kind": "ASSERT",
      "status": "PASS",
      "scope": "BOUNDED",
      "depth": 32,
      "location": {"path": "rtl/counter.sv", "line": 21}
    },
    {
      "name": "p_response",
      "kind": "ASSERT",
      "status": "FAIL",
      "scope": "BOUNDED",
      "depth": 12,
      "counterexample": {
        "path": "artifacts/p_response.vcd",
        "format": "VCD"
      }
    },
    {
      "name": "c_wrap",
      "kind": "COVER",
      "status": "PASS",
      "scope": "BOUNDED",
      "depth": 18,
      "witness": {
        "path": "artifacts/c_wrap.vcd",
        "format": "VCD"
      }
    }
  ]
}
```

Supported property kinds are `ASSERT` and `COVER`. Supported normalized statuses
are `PASS`, `FAIL`, `UNKNOWN`, and `ERROR`. Scope is one of `BOUNDED`,
`UNBOUNDED`, or `UNSPECIFIED`. A bounded result must include `depth`.

## Evidence Semantics

ZDDV does not turn a bounded check into an unbounded proof.

For assertions:

- `PASS + UNBOUNDED` is interpreted as `PROVED`.
- `PASS + BOUNDED` is interpreted as `BOUNDED_SAFE`.
- `PASS + UNSPECIFIED` is retained as `PASS_UNSCOPED`.
- `FAIL` is interpreted as `COUNTEREXAMPLE`.
- `UNKNOWN` and `ERROR` stay explicit.

For covers:

- `PASS` is interpreted as `COVERED`.
- `FAIL` is interpreted as `UNREACHED`.
- `UNKNOWN` and `ERROR` stay explicit.

A counterexample artifact is accepted only for a failed assertion. A witness artifact
is accepted only for a passed cover goal.

Top-level proof status is driven by assertion evidence. Unreached cover goals are
reported as coverage gaps and do not by themselves turn proof status into `FAIL`.

## Current Boundary

This foundation does not yet provide:

- a SymbiYosys, JasperGold, VC Formal, Questa Formal, or other engine adapter;
- proof execution from the CLI;
- SQLite formal-history persistence;
- counterexample waveform parsing;
- formal coverage;
- induction/proof-core semantics beyond evidence supplied by a concrete adapter.

Those are follow-up v0.7 slices.

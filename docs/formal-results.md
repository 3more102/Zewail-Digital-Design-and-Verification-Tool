# Formal Result Evidence

This layer sits above the v0.7 `FormalBackend` API and converts normalized
`FormalCheckResult` data into stable ZDDV evidence records.

## Input contract

The importer accepts JSON matching the existing formal base model:

```json
{
  "backend": "example",
  "engine": "example-formal 1.0",
  "request": {
    "mode": "bmc",
    "depth": 20,
    "properties": [],
    "timeout_s": 30.0
  },
  "command": ["example-formal", "--mode", "bmc"],
  "returncode": 0,
  "status": "FAIL",
  "run_dir": ".zddv/formal/runs/run-1",
  "log_path": ".zddv/formal/runs/run-1/formal.log",
  "properties": [
    {
      "name": "p_safe",
      "kind": "assert",
      "status": "PASS"
    },
    {
      "name": "p_failure",
      "kind": "assert",
      "status": "FAIL",
      "depth": 7,
      "trace_path": "artifacts/p_failure.vcd"
    },
    {
      "name": "c_reachable",
      "kind": "cover",
      "status": "COVERED",
      "trace_path": "artifacts/c_reachable.vcd"
    }
  ]
}
```

## Evidence semantics

ZDDV preserves the proof strength supplied by the adapter instead of upgrading it:

- assertion `PASS` in `bmc` mode with a known request/property depth -> `BOUNDED_SAFE`;
- assertion `PASS` in `bmc` mode without a known depth -> `PASS_BOUNDED_UNSCOPED`;
- assertion `PASS` in `prove` mode -> `PROVED`;
- assertion `FAIL` -> `COUNTEREXAMPLE`;
- cover `COVERED` -> `COVERED`;
- cover `UNCOVERED` -> `UNREACHED`.

A trace attached to a failed assertion is labeled `COUNTEREXAMPLE`. A trace
attached to a covered goal is labeled `WITNESS`. Other traces remain generic
`EVIDENCE` instead of receiving invented semantics.

The result-level status is retained from the backend. Property outcomes are also
summarized independently so later storage/CLI layers can query both without
reinterpreting vendor output.

## File import

`analyze_formal_result_file(project, path)` writes a normalized record to
`.zddv/formal/latest.json` by default. The record includes a snapshot ID, UTC
creation time, project name, input path, execution evidence, property summaries,
trace roles, and retained artifact paths.

## Current boundary

This slice does not execute a formal engine, persist formal history in SQLite,
parse counterexample waveforms, or claim formal coverage. Those remain separate
v0.7 milestones.

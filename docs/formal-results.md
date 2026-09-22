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

## Persistence and CLI

`formal-analyze` imports the normalized JSON contract, writes the reviewable
JSON evidence record, and persists both the snapshot summary and each property/
cover outcome in the project SQLite results database:

```text
zddv --project <project> formal-analyze formal_results.json
zddv --project <project> formal-history --limit 20
zddv --project <project> formal-history --status FAIL --mode bmc
```

Persisted property rows retain the original status, normalized interpretation,
explicit/effective depth, message, and trace path/role. History filters do not
upgrade bounded evidence into an unbounded proof claim.

## Counterexample and witness artifact import

The simulator-independent trace container can also be ingested from normalized
JSON with the CLI:

```text
zddv --project <project> formal-counterexample counterexample.json
zddv --project <project> formal-counterexample witness.json --source adapter-name
```

The normalized artifact is written to
`.zddv/formal/counterexamples/latest.json` by default. It retains the property
name/kind, trace role, signal catalog, ordered steps, optional time/cycle
coordinates, source label, and the SHA-256 of the input file. Signal values stay
as textual logic tokens; ZDDV does not invent radix, signedness, or vendor trace
semantics.

Native VCD formal traces can be normalized without an intermediate JSON translation:

```text
zddv --project <project> formal-vcd-trace trace.vcd \
  --property top.p_safe --kind assert
zddv --project <project> formal-vcd-trace witness.vcd \
  --property top.c_reached --kind cover --signal state
```

The VCD importer preserves hierarchical paths, declaration widths/types/ranges,
four-state textual values, timestamps and timescale metadata. Repeated `--signal`
arguments accept exact paths or unambiguous short names. `--max-steps` is an explicit
memory/evidence bound; exceeding it is an error rather than silent truncation.

## Direct SymbiYosys bounded execution

ZDDV can execute finite-depth safety and cover-reachability jobs directly:

```text
zddv --project <project> formal-bmc --depth 20
zddv --project <project> formal-cover --depth 20
```

Both commands retain the native SBY logfile, generated `.sby` configuration, normalized
JSON evidence, and SQLite history. After a terminal `PASS` or `FAIL`, ZDDV asks SBY for its
machine-readable latest property status with `--statusfmt jsonl --latest`.

For direct BMC runs, only native `ASSERT` rows are normalized. SBY `PASS`/`FAIL` remains
bounded assertion evidence at the requested depth. For direct cover runs, only native
`COVER` rows are normalized: SBY `PASS` maps to `COVERED`, while SBY `FAIL` maps to
`UNCOVERED`. Reported depths and trace paths are retained, so reached covers with traces
become witness evidence.

Malformed, conflicting, unsupported, or failed status queries stay as raw evidence and do
not create normalized property claims. `ERROR` and timeout runs are not queried for
positive property status.

## Native SymbiYosys logfile import

Completed SymbiYosys logs can be imported directly without first translating
them to the normalized JSON contract:

```text
zddv --project <project> formal-sby-analyze <logfile> --mode bmc --depth 20
zddv --project <project> formal-sby-analyze <logfile> --mode cover --depth 20
```

The importer requires an explicit terminal `DONE (..., rc=...)` marker and
does not infer completion from a partial log. It retains the reported result
status and return code, the engine summary when present, explicit failed
assertion names, explicit reached cover statements, and explicitly reported VCD
trace paths. It does not synthesize PASS rows for properties that the logfile
never enumerates, and it does not convert a bounded PASS into an unbounded
proof.

## Cross-run formal cover coverage

Every newly persisted formal snapshot records a SHA-256 design fingerprint over the
configured top plus the discovered formal source paths and file contents. A backend may
also mark `property_set_complete=true` only when the normalized property rows represent
the complete queried property universe. Direct SymbiYosys execution sets that marker only
after a successful machine-readable `--statusfmt jsonl --latest` parse; native logfile
imports remain incomplete because they may list reached goals without enumerating all
unreached goals.

The aggregate command is:

```text
zddv --project <project> formal-coverage
zddv --project <project> formal-coverage --depth 20 --backend sby
```

It writes `.zddv/formal/coverage.json` by default. Snapshots are grouped only when all
of the following are identical: design fingerprint, top, backend, engine, explicit finite
depth, and complete cover-property name universe. Different depths or source revisions are
reported as separate configurations instead of being blended into one percentage.

Inside one compatible group, a goal is aggregate `COVERED` when at least one snapshot
explicitly reports it covered. A goal remains `UNCOVERED` only when every compatible
snapshot reports it uncovered; `UNKNOWN` and `ERROR` stay unresolved evidence.
The report retains per-property covered/uncovered/unknown/error run counts plus every
snapshot ID used in the calculation.

## Current boundary

Native VCD counterexample/witness contents are supported through the explicit
`formal-vcd-trace` importer and are also normalized automatically when a persisted formal
property result directly reports a VCD counterexample or witness. Automatic normalization
uses a fixed 100,000-step evidence guard; parse/size failures are recorded on the trace and
do not change the formal run status. ZDDV does not claim unbounded reachability/proof coverage from
finite-depth evidence. Direct finite-depth SymbiYosys cover-property reachability and
conservative cross-run aggregation are supported. Incomplete property universes,
snapshots without a design fingerprint, different source revisions, different engines,
different depths, or different property sets are never silently merged. Additional
vendor-native waveform formats, automatic trace cross-probing, and broader proof-coverage
metrics remain separate future work.

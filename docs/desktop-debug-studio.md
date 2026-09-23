# Desktop Debug Studio

The v1.1 desktop Debug Studio uses the same persisted ZDDV verification evidence,
deterministic design index, simulator backends, and review gates used by the CLI/core.
Evidence and navigation views are read-only; project-changing or simulator actions are
available only behind explicit SHA-confirmed review gates.

Launch it with:

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 100
```

The `--limit` value bounds recent runs and each detailed evidence query.

## Current views

The desktop provides:

- verification summary cards and recent simulation runs;
- deterministic failure-signature groups;
- an evidence overview for assertions, normalized coverage, formal, and UVM;
- deterministic source-file and source-level hierarchy navigation;
- read-only navigation of recorded waveform signals with bounded in-memory VCD probing;
- a detailed Assertions pane with status, assertion name, run, log line, and message;
- a detailed Formal pane for the newest formal snapshot with property kind, status,
  interpretation, depth, and message;
- a detailed UVM pane for the newest UVM snapshot with severity, report ID, component,
  timestamp, log line, and message;
- an **Actions** pane for SHA-reviewed lint/build/run operations with live project/source
  revalidation before execution;
- a **Generated Review** pane for exact staged SystemVerilog preview and SHA-confirmed
  apply through the existing generated-artifact core gate.

Formal-property and UVM-message database reads are explicitly bounded by the GUI
limit. Assertion events already use the existing bounded history query.

## Trust boundary

Refresh and all evidence/navigation panes only read/rebuild verification state. The
desktop never invokes or transmits data to an AI provider. Lint/build/run execution is
blocked until a deterministic review payload is explicitly approved with its exact
SHA-256, then the live project configuration and matched source bytes are revalidated.

Generated verification drafts are only listed and previewed until the user supplies the
exact staged-content SHA-256 and explicit approval. Apply delegates to
`apply_generated_artifact`, which rejects tamper, project-root escape, writes back into
`.zddv`, and overwrite of existing project files. Applied generated code remains
`execution_enabled=false`; the review pane does not compile or simulate it.

The shared SQLite helpers may initialize or upgrade the local
`.zddv/results.db` schema when opened, consistent with existing reporting commands.

## Desktop milestone status

The v1.1 Debug Studio roadmap items are implemented. Further desktop work should extend
the same deterministic evidence and explicit-review boundaries rather than introduce
GUI-only simulator or mutation logic.

# Desktop Debug Studio

The v1.1 desktop Debug Studio is a display-only view over the same persisted ZDDV
verification evidence and deterministic design index used by the CLI.

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
- a detailed Assertions pane with status, assertion name, run, log line, and message;
- a detailed Formal pane for the newest formal snapshot with property kind, status,
  interpretation, depth, and message;
- a detailed UVM pane for the newest UVM snapshot with severity, report ID, component,
  timestamp, log line, and message;
- recorded waveform signal browsing with bounded in-memory VCD value probing;
- an Actions pane for structured lint/build/run requests that requires exact SHA-256
  confirmation of the reviewed request before execution.

Formal-property and UVM-message database reads are explicitly bounded by the GUI
limit. Assertion events already use the existing bounded history query.

## Trust boundary

Browsing and Refresh read persisted verification evidence and rebuild the in-memory
design index. They do not launch simulations or formal jobs, invoke or transmit data
to an AI provider, stage/apply generated artifacts, or edit RTL, testbench sources,
or project configuration.

The Actions pane is a separate explicit execution boundary. It accepts only the
structured actions `lint`, `build`, and `run`; it exposes no arbitrary shell-command
field. Preparing an action computes a canonical request SHA-256. Execution is rejected
unless the user re-enters that exact SHA-256 and the reviewed project identity still
matches the active project. A `run` may build through the existing simulator backend
when its normal core API requires it.

The shared SQLite helpers may initialize or upgrade the local
`.zddv/results.db` schema when opened, consistent with existing reporting commands.
Therefore "display-only" describes verification/project actions rather than a
guarantee of zero filesystem writes.

## v1.1 desktop milestone

The planned v1.1 desktop slices are implemented: evidence/detail views, deterministic
source and hierarchy navigation, persisted elaborated hierarchy browsing, recorded
waveform probing, and review-gated structured project actions. Future desktop work
should continue to reuse the existing core APIs rather than duplicate simulator-
specific logic in the GUI.

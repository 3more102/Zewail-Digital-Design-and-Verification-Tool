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
- persisted elaborated-hierarchy browsing and read-only recorded-waveform navigation with bounded VCD probing;
- a detailed Assertions pane with status, assertion name, run, log line, and message;
- a detailed Formal pane for the newest formal snapshot with property kind, status,
  interpretation, depth, and message;
- a detailed UVM pane for the newest UVM snapshot with severity, report ID, component,
  timestamp, log line, and message;
- an Actions pane for structured `lint`, `build`, and `run` requests that must be prepared,
  reviewed as canonical JSON, and confirmed by re-entering the exact SHA-256 before execution.

Formal-property and UVM-message database reads are explicitly bounded by the GUI
limit. Assertion events already use the existing bounded history query.

## Trust boundary

Refresh reads persisted verification evidence and rebuilds the in-memory design
index. Refresh itself does not launch simulations or formal jobs, invoke or transmit
data to an AI provider, stage/apply generated artifacts, or edit RTL, testbench
sources, or project configuration.

The Actions pane is a separate explicit execution boundary. It supports only
structured `lint`, `build`, and `run` requests through existing ZDDV core APIs.
Preparing an action executes nothing. The prepared request includes the active project
name/root/simulator/top plus exact run parameters, is rendered for review, and is
bound to a canonical SHA-256. Execution is rejected unless the full SHA is re-entered
and the active project identity still matches. Arbitrary shell commands are not
accepted.

The shared SQLite helpers may initialize or upgrade the local
`.zddv/results.db` schema when opened, consistent with existing reporting commands.
Therefore "display-only" describes verification/project actions rather than a
guarantee of zero filesystem writes.

## v1.1 desktop milestone status

The planned v1.1 desktop milestones are implemented: evidence browsing, source and
elaborated hierarchy navigation, bounded waveform probing, detailed assertion/formal/UVM
views, and SHA-confirmed structured project actions. Future desktop actions should
continue to use existing core APIs and explicit review gates rather than introduce an
arbitrary command surface.

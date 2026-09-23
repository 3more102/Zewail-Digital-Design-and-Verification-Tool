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
- read-only navigation of recorded waveform signals with bounded in-memory VCD probing;
- a detailed Assertions pane with status, assertion name, run, log line, and message;
- a detailed Formal pane for the newest formal snapshot with property kind, status,
  interpretation, depth, and message;
- a detailed UVM pane for the newest UVM snapshot with severity, report ID, component,
  timestamp, log line, and message;
- a Review Actions pane that can stage proposal JSON under `.zddv/generated` and apply
  exact reviewed SystemVerilog bytes only after manual SHA-256 confirmation and an
  explicit reviewed opt-in.

Formal-property and UVM-message database reads are explicitly bounded by the GUI
limit. Assertion events already use the existing bounded history query.

## Trust boundary

Refresh and waveform browsing read persisted verification evidence and rebuild
in-memory views. They do not launch simulations or formal jobs, invoke or transmit
data to an AI provider, or edit RTL/testbench/project configuration.

The Review Actions pane is the deliberate exception to the otherwise read-only
desktop. Staging writes only to `.zddv/generated`. Applying calls the existing
`apply_generated_artifact` core API and is blocked unless the user manually supplies
the exact 64-character SHA-256 of the reviewed staged bytes and explicitly confirms
review. The core still refuses changed draft bytes, paths outside the project, writes
back into `.zddv`, unsupported suffixes, and overwrites of existing source files.
Applying copies bytes only; it does not compile or execute them.

The shared SQLite helpers may initialize or upgrade the local
`.zddv/results.db` schema when opened, consistent with existing reporting commands.
Therefore "display-only" describes verification/project actions rather than a
guarantee of zero filesystem writes.

## v1.1 desktop milestone status

Waveform navigation, detailed evidence panes, and review-gated generated-artifact
actions are integrated through the existing core APIs. Further desktop work can build
on these panes without weakening their evidence or approval semantics.

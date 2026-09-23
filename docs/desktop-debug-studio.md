# Desktop Debug Studio

The v1.1 desktop Debug Studio is a display-only view over the same persisted ZDDV
verification evidence used by CLI reports, signoff, and release workflows.

Launch it with:

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 100
```

The `--limit` value bounds the recent-run list and each detailed evidence query.

## Views

The desktop currently provides:

- verification summary cards and recent simulation runs;
- deterministic failure-signature groups;
- an evidence overview for assertions, normalized coverage, formal, and UVM;
- a detailed Assertions pane with normalized status, assertion name, run, log line,
  and message;
- a detailed Formal pane for the newest formal snapshot, including property kind,
  status, interpretation, depth, and message;
- a detailed UVM pane for the newest UVM snapshot, including severity, report ID,
  component, timestamp, log line, and message.

Formal-property and UVM-message queries are explicitly bounded by the GUI limit. This
prevents the desktop detail view from requiring an unbounded evidence materialization.

## Trust boundary

Refresh only reads persisted verification evidence through ZDDV's shared storage
APIs. It does not launch simulation or formal jobs, invoke or transmit data to an AI
provider, or stage/apply generated artifacts. It also does not edit RTL, testbench
sources, or project configuration.

The shared SQLite storage helpers may initialize or upgrade the local
`.zddv/results.db` schema when opened, matching existing reporting commands. Thus
"display-only" describes verification/project actions rather than a promise of zero
filesystem writes.

## Remaining desktop milestones

The roadmap still retains source/hierarchy navigation, waveform/targeted-probe
integration, and review-gated project actions. Those should continue to call the
existing core APIs rather than duplicate simulator-specific logic inside the GUI.

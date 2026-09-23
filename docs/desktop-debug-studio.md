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
  timestamp, log line, and message.

Formal-property and UVM-message database reads are explicitly bounded by the GUI
limit. Assertion events already use the existing bounded history query. Persisted
simulator-elaborated hierarchy is displayed only when its project/top/simulator identity
and design-revision fingerprint match the active RTL/config state; stale evidence is
reported as `STALE` and is not regenerated implicitly.

## Trust boundary

Refresh reads persisted verification evidence and rebuilds the in-memory design
index. It does not launch simulations or formal jobs, invoke or transmit data to an
AI provider, stage/apply generated artifacts, or edit RTL, testbench sources, or
project configuration.

The shared SQLite helpers may initialize or upgrade the local
`.zddv/results.db` schema when opened, consistent with existing reporting commands.
Therefore "display-only" describes verification/project actions rather than a
guarantee of zero filesystem writes.

## v1.1 desktop milestone status

The planned v1.1 desktop milestones are implemented: evidence browsing, source and
elaborated hierarchy navigation, bounded waveform probing and cross-probing, detailed
assertion/formal/UVM views, and SHA-confirmed review-gated project actions.

# Desktop Debug Studio

The v1.1 desktop Debug Studio uses the same persisted ZDDV verification evidence,
deterministic design index, and core execution APIs used by the CLI. Debug/navigation
views are read-only; project actions are isolated behind explicit SHA-confirmed review.

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
- deterministic source-file and source-level hierarchy navigation with read-only RTL preview;
- hierarchy-to-source cross-navigation limited to files authorized by the current design index;
- read-only navigation of recorded waveform signals with bounded in-memory VCD probing;
- a detailed Assertions pane with status, assertion name, run, log line, and message;
- a detailed Formal pane for the newest formal snapshot with property kind, status,
  interpretation, depth, and message;
- a detailed UVM pane for the newest UVM snapshot with severity, report ID, component,
  timestamp, log line, and message;
- an Actions pane for SHA-confirmed lint, build, run, and exact historical rerun.

Formal-property and UVM-message database reads are explicitly bounded by the GUI
limit. Assertion events already use the existing bounded history query.

## Trust boundary

Refresh and navigation read persisted verification evidence, the in-memory design
index, and explicitly indexed RTL sources. They do not launch simulations or formal
jobs, invoke or transmit data to an AI provider, stage/apply generated artifacts, or
edit RTL, testbench sources, or project configuration.

The Actions pane is separate from refresh/navigation. Preparing an action does not
execute it. Execution requires the exact reviewed SHA-256 plus an explicit approval
checkbox, then revalidates the project configuration and source fingerprints. A
historical rerun also binds the selected persisted run record and its recorded
test/seed/plusargs/timeout into that review payload before delegating to the shared
rerun core.

The shared SQLite helpers may initialize or upgrade the local
`.zddv/results.db` schema when opened, consistent with existing reporting commands.
Therefore "display-only" describes verification/project actions rather than a
guarantee of zero filesystem writes.

## Action scope

The current desktop action surface is deliberately limited to lint, build, run, and
exact historical rerun through existing core APIs. Generated-verification artifact
application remains in its existing review-gated core/CLI workflow and is not exposed
as an automatic desktop action.

# Desktop Debug Studio

ZDDV v1.1 introduces a desktop Debug Studio as a display-only consumer of the same
persisted verification evidence used by the CLI, HTML report, signoff, and release
flows.

Launch it from a ZDDV project:

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 250
```

## Current views

The first desktop foundation provides:

- total/pass/fail/timeout run statistics and pass rate;
- recent persisted simulation runs;
- deterministic failure-signature groups;
- per-test pass/fail/timeout statistics;
- latest normalized coverage, including point-count snapshots and native percentage
  score snapshots;
- normalized assertion statistics;
- the newest persisted formal result snapshot;
- the newest persisted UVM log snapshot.

The GUI uses the existing simulator-independent storage APIs. It does not parse a
separate private database format or create simulator-specific verification truth.

## Trust boundary

The GUI is intentionally display-only. The Refresh button re-reads persisted ZDDV
evidence. It does not:

- launch simulations or regressions;
- launch formal verification;
- invoke an AI provider or transmit project data;
- stage or apply generated verification artifacts;
- edit RTL, testbench sources, or `zddv.toml`.

As with the existing ZDDV storage/reporting commands, opening storage through the
shared helpers may create or upgrade the local `.zddv/results.db` schema. Therefore
"display-only" refers to verification/project actions, not a guarantee that SQLite
performs zero filesystem writes.

## Platform requirements

The desktop uses Python's standard `tkinter`/Tk toolkit and adds no Python package
dependency. A minimal/headless Python installation may omit Tk or have no display
server. In that case `zddv gui` reports an explicit runtime error while all
non-GUI ZDDV commands remain available.

## Next milestones

Planned desktop work remains:

- source and hierarchy navigation;
- waveform navigation and targeted probing;
- evidence-to-RTL cross-probing;
- protocol detail panes;
- review-gated project actions that call the existing core APIs rather than
  duplicating execution logic in the GUI.

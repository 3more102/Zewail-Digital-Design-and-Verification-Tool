# Desktop Debug Studio

The v1.1+ desktop Debug Studio uses the same ZDDV verification evidence, deterministic
design index, waveform/cross-probe engines, and reviewed core actions as the CLI.

Launch it with:

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 100
```

The `--limit` value bounds recent runs and detailed persisted-evidence queries.

## Current views

The desktop provides:

- verification summary cards, recent simulation runs, and failure-signature groups;
- assertion, coverage, formal, and UVM evidence views;
- source/hierarchy navigation with read-only RTL preview authorized by the current design index;
- persisted elaborated hierarchy that is rejected when its design-revision fingerprint is stale;
- recorded-waveform navigation, bounded VCD probing, and hierarchy/source/connectivity cross-probing;
- detailed Assertions, Formal, and UVM panes;
- an `Actions` pane for SHA-confirmed lint/build/run operations through existing core APIs;
- a `Generated Review` pane for exact-byte review and SHA-confirmed apply of already staged generated verification drafts.

## Trust boundary

Browsing, refresh, source preview, and waveform/cross-probe operations do not launch
simulation/formal work or invoke AI. Source preview is restricted to paths authorized
by the current deterministic design index.

The `Actions` pane is an explicit execution boundary. Preparing an action is
review-only. Execution requires the exact review SHA-256 plus explicit approval, and
ZDDV revalidates project configuration, matched source paths/bytes, and runtime
parameters immediately before calling the existing lint/build/run core API.

The `Generated Review` pane is a separate source-mutation boundary. It only lists
staged generated-verification drafts under `.zddv/generated/drafts`, re-hashes the
exact staged bytes, and delegates apply to the existing `apply_generated_artifact`
core gate. Apply requires explicit reviewed-content approval and the exact content
SHA-256. The core still blocks tampered drafts, project-root escape, writes into
`.zddv`, unsupported targets, and overwriting existing files. Applying a draft does
not compile, simulate, or enable execution of the generated code.

The shared SQLite helpers may initialize or upgrade the local
`.zddv/results.db` schema when opened, consistent with existing reporting commands.

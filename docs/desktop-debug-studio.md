# Desktop Debug Studio

The v1.1 Desktop Debug Studio reuses the same persisted ZDDV evidence, deterministic
design index, simulator adapters, and generated-artifact gates as the CLI.

Launch it with:

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 100
```

The `--limit` value bounds recent runs and detailed evidence queries.

## Current views and actions

The desktop provides read-only verification summary, run/failure, source/hierarchy,
elaborated hierarchy, waveform/cross-probe, assertion, formal, and UVM views.
Persisted elaborated hierarchy is accepted only when its project/top/simulator identity
and design-revision fingerprint still match the active project.

The **Actions** tab exposes lint, build, and one simulation run through existing core
APIs. Preparing an action does not execute it. Execution requires explicit approval
plus the exact review SHA-256, which binds project configuration, matched source bytes,
the selected action, and runtime parameters. ZDDV rebuilds that review state immediately
before execution and blocks execution if it changed.

The **Review Actions** tab exposes the generated-verification workflow. Proposals are
staged through the authoritative staging API under `.zddv/generated/drafts`. The tab
previews and re-hashes the exact staged bytes. Applying a draft requires explicit
approval and the exact content SHA-256, and final application is delegated to the
existing core apply gate. Changed, missing, invalid, execution-enabled, or already
applied drafts are refused.

## Trust boundary

Evidence refresh, source preview, hierarchy navigation, waveform probing, and persisted
evidence inspection remain read-only. Source preview is authorized by the current
deterministic design index rather than arbitrary filesystem paths.

Lint/build/run can execute only through the SHA-confirmed Actions gate. Generated
artifacts can modify project sources only through the separate exact-content
SHA-confirmed apply gate. The desktop does not invoke AI, does not automatically run
verification, and does not automatically apply or execute generated code.

The shared SQLite helpers may initialize or upgrade the local `.zddv/results.db`
schema when opened, consistent with existing reporting commands.

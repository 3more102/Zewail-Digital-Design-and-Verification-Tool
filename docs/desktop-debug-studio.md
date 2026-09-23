# Desktop Debug Studio

The v1.1+ Desktop Debug Studio reuses the same persisted ZDDV evidence,
deterministic design index, simulator adapters, and generated-artifact review gates
as the CLI.

Launch it with:

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 100
```

The `--limit` value bounds recent runs and detailed persisted-evidence queries.

## Current views and actions

Evidence-oriented tabs remain read-only: verification summary, runs and failure
groups, source/hierarchy navigation, persisted elaborated hierarchy, waveform
probing/cross-probing, assertions, formal evidence, and UVM evidence. Source preview
is authorized by the current deterministic design index. Persisted elaboration is
used only when its project/top/simulator identity and design-revision fingerprint
match the active design; stale evidence is reported as `STALE`.

The **Actions** tab exposes lint, build, and a single simulation run through existing
core APIs. Preparing an action does not execute it. Execution requires explicit
approval plus the exact review SHA-256, which binds the reviewed project
configuration, source bytes, selected action, and runtime parameters. ZDDV
revalidates that state immediately before execution.

The **Review Actions** tab handles generated verification drafts. Proposals are staged
through the existing generated-artifact staging API under
`.zddv/generated/drafts`. The pane previews and re-hashes the exact staged bytes.
Apply requires explicit reviewed-content approval and the exact content SHA-256, then
delegates to the existing `apply_generated_artifact` core gate. Changed, missing,
invalid, or already-applied drafts are refused.

## Trust boundary

Evidence browsing does not launch simulation/formal work, invoke AI, or mutate RTL.
Lint/build/run can execute only through the SHA-confirmed **Actions** gate. Generated
artifacts can modify project sources only through the separate exact-content
SHA-confirmed **Review Actions** gate.

Neither action surface performs automatic execution or automatic apply. Applying a
generated draft does not compile, simulate, or enable execution of the generated
code. Existing core safeguards still reject tampered bytes, invalid destinations,
writes back into `.zddv`, and overwrites of existing source files.

The shared SQLite helpers may initialize or upgrade the local
`.zddv/results.db` schema when opened, consistent with existing reporting commands.

## Desktop milestone status

The planned desktop milestones are implemented: evidence browsing, source and
elaborated hierarchy navigation with freshness checks, bounded waveform
probing/cross-probing, detailed assertion/formal/UVM views, SHA-confirmed
lint/build/run actions, and SHA-confirmed generated-artifact review/apply.

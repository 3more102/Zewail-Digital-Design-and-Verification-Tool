# Desktop Debug Studio

The Desktop Debug Studio uses the same ZDDV project model, persisted verification
evidence, deterministic design index, and core execution/review APIs as the CLI.

Launch it with:

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 100
```

The `--limit` value bounds recent runs and detailed evidence/review queries.

## Evidence and debug views

Normal refresh is display-only with respect to verification execution and project-source
mutation. The desktop provides summary cards, recent runs, normalized failure groups,
assertion/formal/UVM evidence, deterministic source/hierarchy navigation, persisted
simulator-elaborated hierarchy, RTL preview, waveform navigation, bounded VCD probing,
and waveform-to-hierarchy/source/connectivity cross-probing.

Source preview is authorized by the current deterministic design index. Persisted
simulator-elaborated hierarchy is used only when its project/top/simulator identity and
design-revision fingerprint match the active RTL/config state; stale evidence is reported
and not regenerated implicitly.

The snapshot `policy` object is scoped to `snapshot_refresh`. Its legacy
`display_only=true`, `executes_verification=false`, and
`applies_generated_artifacts=false` fields describe refresh itself, not every control
available in the desktop. Capability flags separately report the gated action surfaces.

## Actions

The **Actions** pane exposes existing lint/build/run core operations. Preparing an action
does not execute it. Execution requires the exact review SHA-256 plus explicit approval.
Immediately before execution, ZDDV revalidates the live project configuration, matched
source set and bytes, and reviewed runtime parameters. Any drift invalidates the review.

## Review Actions

The generated-artifact **Review Actions** pane is separate from execution Actions. It
lists staged assertion/test drafts, re-hashes their exact bytes, and provides a bounded
preview. Drafts are rejected unless the manifest preserves the review safeguards,
including `auto_apply=false` and `execution_enabled=false`.

Applying a draft requires an exact typed content SHA-256 and explicit approval, then
delegates to the existing `apply_generated_artifact` core gate. That gate rejects
changed bytes, path escapes, writes back into `.zddv`, unsupported suffixes, and
existing-file overwrite. Apply copies reviewed bytes only; it does not compile or execute
the generated artifact.

## Trust boundary

- Evidence/source/waveform refresh does not launch verification or mutate project sources.
- **Actions** may execute lint/build/run only after exact review and live-state
  revalidation.
- **Review Actions** may stage under `.zddv/generated` and copy exact reviewed
  `.sv`/`.svh` bytes into a new project path only after SHA confirmation.
- Neither desktop action path automatically invokes AI.

Shared SQLite helpers may initialize or upgrade `.zddv/results.db` when opened,
consistent with existing reporting commands.

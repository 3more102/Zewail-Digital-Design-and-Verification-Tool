# Desktop Debug Studio

The v1.1 Desktop Debug Studio uses the same ZDDV project model, persisted verification
evidence, deterministic design index, and simulator/core APIs as the CLI.

Launch it with:

```bash
zddv --project my_project gui
zddv --project my_project gui --limit 100
```

The `--limit` value bounds recent runs and detailed evidence/draft queries.

## Read-only evidence and debug views

The desktop provides verification summary cards, recent runs, normalized failure groups,
assertion/formal/UVM evidence, deterministic source and hierarchy navigation, persisted
simulator-elaborated hierarchy, RTL preview, recorded-waveform navigation, bounded VCD
probing, and waveform-to-hierarchy/source/connectivity cross-probing.

Source preview is authorized by the current deterministic design index rather than by an
arbitrary filesystem path. Persisted elaborated hierarchy is accepted for cross-probing
only when its project/top/simulator identity and design-revision fingerprint still match
the active RTL/testbench inputs.

## Actions

The **Actions** pane exposes existing lint/build/run core operations. Preparing an action
does not execute it. Execution requires the exact review SHA-256 plus explicit approval,
and ZDDV re-hashes the live configuration, source set/bytes, and runtime parameters before
execution. Drift invalidates the review and blocks the action.

## Generated Review

The **Generated Review** pane is separate from execution Actions. It lists staged generated
assertion/test drafts, re-hashes their exact bytes, offers a bounded source preview, and
leaves the SHA confirmation field blank. Applying a draft requires explicit approval and
the exact typed content SHA-256, then delegates to the existing
`apply_generated_artifact` core gate.

Generated apply refuses changed bytes, paths outside the project, writes back into
`.zddv`, unsupported suffixes, and existing-file overwrite. Applying copies bytes only;
it does not compile or execute the generated artifact.

## Trust boundary

Normal evidence/source/waveform browsing is read-only with respect to verification and
project sources. The two explicit mutation/execution surfaces are review-gated:

- **Actions** may execute lint/build/run after exact review and live-state revalidation.
- **Generated Review** may stage under `.zddv/generated` and copy exact reviewed
  `.sv`/`.svh` bytes into a new project path after SHA confirmation.

Neither desktop path invokes AI. Shared SQLite helpers may initialize or upgrade
`.zddv/results.db` when opened, consistent with existing reporting commands.

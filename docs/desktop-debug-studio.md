# Desktop Debug Studio

The Desktop Debug Studio combines read-only evidence/debug views with explicitly
review-gated actions built on the same ZDDV core APIs used by the CLI. Evidence refresh
does not execute verification or mutate project sources; execution or generated-code
application is exposed only through separate SHA-confirmed review flows.

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
- an **Actions** pane for SHA-confirmed lint/build/run requests bound to the active
  project configuration, source hashes, and reviewed runtime parameters;
- a **Review Actions** pane for staged generated assertion/test drafts with bounded
  exact-byte preview, integrity re-hashing, explicit approval, and manual content
  SHA-256 confirmation before application.

Formal-property and UVM-message database reads are explicitly bounded by the GUI
limit. Assertion events already use the existing bounded history query. Persisted
simulator-elaborated hierarchy is displayed only when its project/top/simulator identity
and design-revision fingerprint match the active RTL/config state; stale evidence is
reported as `STALE` and is not regenerated implicitly.

## Trust boundary

Evidence refresh reads persisted verification evidence and rebuilds the in-memory
design index. Refresh does not launch simulations or formal jobs, invoke or transmit
data to an AI provider, stage/apply generated artifacts, or edit RTL, testbench
sources, or project configuration.

The **Actions** pane is a separate execution boundary: preparation is non-executing,
the exact project/config/source/action state is hashed, and execution requires both
manual SHA-256 confirmation and explicit approval. The live project is revalidated
immediately before execution.

The **Review Actions** pane is a separate generated-code boundary: it can stage a
proposal into the existing isolated `.zddv/generated` review area and can apply exact
reviewed bytes only through the existing generated-artifact core gate. Apply requires
an intact review-required draft, `auto_apply=false`, `execution_enabled=false`,
manual exact SHA-256 entry, and explicit approval. Apply never chains compilation or
simulation.

The shared SQLite helpers may initialize or upgrade the local `.zddv/results.db`
schema when opened, consistent with existing reporting commands.

## v1.1 desktop milestone status

The planned v1.1 desktop milestones are implemented: evidence browsing, source and
elaborated hierarchy navigation, bounded waveform probing and cross-probing, detailed
assertion/formal/UVM views, and SHA-confirmed review-gated project actions.


## v1.2 generated-review status

The first v1.2 Desktop review workflow is implemented. Generated verification drafts
are discovered and re-hashed without source mutation, previews are bounded, changed or
already-applied drafts fail closed, and the final copy into a new SystemVerilog project
path remains delegated to the existing SHA-confirmed core API.

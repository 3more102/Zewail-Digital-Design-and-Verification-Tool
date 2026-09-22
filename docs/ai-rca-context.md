# AI RCA Context

ZDDV's AI-assisted debug foundation starts with a deterministic, provider-neutral
context bundle rather than a direct model call.

## Command

```text
zddv --project <project> ai-rca-context --run <run-id>
```

The default output is:

```text
.zddv/debug/ai-rca-context.json
```

## What the bundle contains

The bundle is composed from existing ZDDV evidence:

- normalized run metadata and failure signature;
- ranked root-cause candidates;
- the score basis and retained evidence for each included candidate;
- evidence-backed debug-probe suggestions;
- explicit probe blockers and analysis limitations;
- a canonical SHA-256 digest of the evidence payload.

The candidate limit is review-oriented. It does not change the source ranking.

## Safety and evidence boundary

Creating the bundle does not:

- contact an external AI or model provider;
- transmit project files or diagnostics;
- execute a proposed command;
- generate or apply assertions/tests;
- convert `evidence_score` into a probability of causation.

The bundle records these boundaries in machine-readable policy fields. It also carries
a prompt contract for a future downstream assistant: use only supplied evidence,
separate observed facts from hypotheses and unknowns, cite candidate/evidence
references, and avoid inventing signal values, source lines, protocol behavior, or
tool results.

Before any future external provider integration is enabled, ZDDV should require an
explicit opt-in and preserve the existing review gates for generated verification
artifacts and proposed debug actions.

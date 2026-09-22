# AI Provenance Chain Audit

ZDDV can re-verify the persisted AI-assisted debug chain without invoking a
model, transmitting data, staging generated code, modifying project sources, or
executing commands.

## Command

```bash
zddv --project my_project ai-chain-audit \
  --context .zddv/debug/ai-rca-context.json \
  --response .zddv/ai/provider-response.json \
  --validated .zddv/ai/validated-response.json \
  --review .zddv/ai/reviews/<review-id>.json
```

The `--review` input is optional. Without it, the audit verifies the chain
through the schema-validated response.

Audit reports are written under `.zddv/ai/audits` by default.

## What is verified

The audit recomputes and cross-checks:

- the canonical SHA-256 of the deterministic RCA context evidence;
- the canonical model-request SHA-256 reconstructed from that context;
- the raw provider-response file SHA-256;
- the validated response's exact context, request, raw-response, and provider
  provenance links;
- the strict response schema and all evidence references again;
- the canonical validated-payload SHA-256;
- when a review record is supplied, its approved status, validated-response
  path, payload SHA-256, and generated-proposal count.

A mismatch aborts the audit before a PASS report is written.

## Trust boundary

A PASS result means the supplied ZDDV artifacts form a self-consistent
provenance chain at audit time. SHA-256 consistency detects changes relative to
the recorded chain, but it is not a cryptographic authenticity signature and it
does not establish that a model hypothesis is correct.

The audit is read-only with respect to the verification project. It does not:

- invoke an AI model;
- perform external transmission;
- export or stage generated verification code;
- modify configured sources;
- compile or simulate;
- execute commands.

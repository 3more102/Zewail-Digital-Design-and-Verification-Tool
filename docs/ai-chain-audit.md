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


## Portable audit bundle

After the chain is approved and one or more reviewed proposals have been exported,
ZDDV can package the complete provenance chain into a deterministic ZIP archive:

\`\`\`bash
zddv --project my_project ai-audit-bundle-export \
  --context .zddv/debug/ai-rca-context.json \
  --response .zddv/ai/provider-response.json \
  --validated .zddv/ai/validated-response.json \
  --review .zddv/ai/reviews/<review-id>.json \
  --proposal .zddv/ai/proposals/<proposal>.json
\`\`\`

Repeat \`--proposal\` to include multiple reviewed proposals. Proposal exports now
record their exact validated-payload proposal index, so the bundle can bind each
proposal to the reviewed payload without relying on filenames.

The archive contains stable \`audit/\` member paths, exact original artifact bytes,
a SHA-256 inventory, and a portable chain manifest. ZIP timestamps are fixed,
members are stored without compression, and member order is lexicographic, so
identical inputs produce byte-identical archives.

The bundle can be moved to another machine and verified without the original
project paths:

\`\`\`bash
zddv ai-audit-bundle-verify my_project/.zddv/ai/audits/ai-audit-bundle.zip
\`\`\`

Portable verification recomputes the context evidence hash, model-request hash,
raw-response hash, validated payload and provenance links, approved review link,
and every selected proposal's review ID, payload SHA-256, proposal index, and file
SHA-256. Embedded absolute source paths are treated as historical metadata rather
than relocation requirements.

A VERIFIED result establishes internal provenance integrity of the bundled ZDDV
artifacts. SHA-256 is not an authenticity signature, and the bundle does not claim
that model hypotheses or generated proposals are technically correct.

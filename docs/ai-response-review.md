# Schema-validated AI response review gate

Raw provider output cannot enter ZDDV's generated-verification workflow directly.
This boundary validates the response schema and every evidence reference, then
requires a separate human approval tied to the exact validated SHA-256. Before a
saved RCA context can be used for provider invocation or response ingestion, ZDDV
also recomputes the canonical SHA-256 of its evidence payload and rejects the file
if it no longer matches the recorded provenance digest.

## Expected response

The provider response content must be strict JSON with schema_version 1,
analysis set to zddv_ai_rca_response, and these arrays:

- observed_facts: statement plus evidence_refs
- hypotheses: statement plus evidence_refs
- unknowns: non-empty strings
- next_checks: description plus evidence_refs
- generated_proposals: SystemVerilog assertion/test proposal plus evidence_refs

Free-form prose and Markdown fences are rejected. Evidence references must
resolve to the exact context bundle. Supported forms are run:<run-id>,
candidate:<rank>, probe:<rank>, and limitation:<index>.

## Workflow

1. Build deterministic evidence with ai-rca-context.
2. Write and review the exact local request with ai-provider-request.
3. Invoke a provider explicitly with ai-provider-run, supplying both
   --allow-external and the reviewed request SHA-256.
4. Run ai-response-ingest with the raw response and the exact context file.
5. Review the validated payload and record approval with ai-response-review,
   passing the exact validated payload SHA-256 and --approve-reviewed.
6. Export one approved proposal with ai-proposal-export.
7. Use generated-stage separately on the exported proposal.

Validation does not make a hypothesis true. Approval records review of the exact
validated payload; it still does not stage, apply, compile, simulate, or execute
generated code automatically.

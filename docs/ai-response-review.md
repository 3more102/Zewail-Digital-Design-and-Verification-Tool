# Schema-validated AI response review gate

Raw provider output cannot enter ZDDV's generated-verification workflow directly.
This boundary validates the response schema and every evidence reference, then
requires a separate human approval tied to the exact validated SHA-256.

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
2. Invoke a provider explicitly with ai-provider-run.
3. Run ai-response-ingest with the raw response and the exact context file.
4. Review the validated payload and record approval with ai-response-review,
   passing the exact validated payload SHA-256 and --approve-reviewed.
5. Export one approved proposal with ai-proposal-export.
6. Use generated-stage separately on the exported proposal.

Validation does not make a hypothesis true. Approval records review of the exact
validated payload; it still does not stage, apply, compile, simulate, or execute
generated code automatically.

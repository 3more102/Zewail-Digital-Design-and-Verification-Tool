# AI model-provider adapter boundary

ZDDV's AI-assisted debugging path is intentionally split into separate trust
boundaries.

1. `zddv ai-rca-context` builds deterministic, provider-neutral evidence.
2. `zddv ai-provider-run` is an explicit invocation boundary. External
   transmission is refused unless the user supplies `--allow-external`.
3. Provider output is stored as `ai_provider_response_raw` and remains
   untrusted at the invocation boundary.
4. `zddv ai-response-ingest` separately validates the model JSON contract,
   verifies the reviewed evidence SHA-256, and resolves every hypothesis
   evidence reference against the supplied RCA context.
5. Neither raw nor schema-validated output can automatically stage generated
   verification code, modify project sources, run commands, compile, or simulate.
6. The remaining v0.9 milestone is an explicit human-review gate before any
   model-proposed action or generated assertion/test can enter an apply path.

## Built-in provider

The first built-in adapter is `openai-compatible`, targeting a
chat-completions-compatible HTTP endpoint. It uses Python's standard library and
adds no runtime dependency.

Remote endpoints must use HTTPS. Plain HTTP is accepted only for loopback-local
endpoints such as `127.0.0.1` or `localhost`. API keys are read only from an
environment variable named by `--api-key-env`; the key is never written into
ZDDV artifacts.

Example:

```bash
zddv --project my_project ai-rca-context --run <run-id>

zddv --project my_project ai-provider-run \
  --context .zddv/debug/ai-rca-context.json \
  --provider openai-compatible \
  --endpoint https://provider.example/v1/chat/completions \
  --model provider-model \
  --api-key-env PROVIDER_API_KEY \
  --allow-external
```

For a local OpenAI-compatible server:

```bash
zddv --project my_project ai-provider-run \
  --context .zddv/debug/ai-rca-context.json \
  --provider openai-compatible \
  --endpoint http://127.0.0.1:8000/v1/chat/completions \
  --model local-model \
  --allow-external
```

Even for loopback endpoints, the explicit flag is retained because invocation is
a deliberate user action and ZDDV does not silently contact model services.


## Response ingestion

The provider request now includes a machine-readable response contract. A conforming
model response uses `analysis = "zddv_ai_rca_response"`, repeats the exact reviewed
`context_evidence_sha256`, and supplies hypotheses whose `evidence_refs` resolve to
existing context evidence.

Supported evidence references are:

- `{"kind":"candidate","rank":N}`
- `{"kind":"debug_probe","rank":N}`
- `{"kind":"failure_signature"}`
- `{"kind":"limitation","index":N}`

Example model content:

```json
{
  "schema_version": 1,
  "analysis": "zddv_ai_rca_response",
  "context_evidence_sha256": "<reviewed-evidence-sha256>",
  "hypotheses": [
    {
      "id": "H1",
      "summary": "Inspect the ranked RTL driver first.",
      "evidence_refs": [
        {"kind": "candidate", "rank": 1},
        {"kind": "debug_probe", "rank": 1}
      ],
      "unknowns": ["Causality is not established."],
      "next_checks": ["Run the referenced waveform probe."]
    }
  ],
  "overall_unknowns": [],
  "next_checks": []
}
```

Validate and resolve the raw response with:

```bash
zddv --project my_project ai-response-ingest \
  --response .zddv/ai/provider-response.json \
  --context .zddv/debug/ai-rca-context.json
```

The validated artifact defaults to
`.zddv/ai/validated-response.json`. Validation means only that the response
matches the accepted structure, is linked to the reviewed evidence digest, and
contains resolvable evidence references. Hypotheses remain untrusted and require
human review.

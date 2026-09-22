# AI model-provider adapter boundary

ZDDV's AI-assisted debugging path is intentionally split into separate trust
boundaries.

1. `zddv ai-rca-context` builds deterministic, provider-neutral evidence.
2. `zddv ai-provider-request` writes the exact provider-neutral request
   locally and prints its SHA-256 without contacting a model provider.
3. `zddv ai-provider-run` is an explicit invocation boundary. External
   transmission is refused unless the user supplies both `--allow-external`
   and the exact reviewed request SHA-256.
4. Provider output is stored as `ai_provider_response_raw` and remains
   untrusted until `ai-response-ingest` validates its schema, evidence
   references, and request/context provenance.
5. `ai-response-review` requires explicit human approval tied to the exact
   validated payload SHA-256 before reviewed proposals can be exported.
6. No AI path automatically stages generated verification code, modifies
   project sources, runs commands, compiles, or simulates. Exported proposals
   still enter the separate SHA-confirmed `generated-stage` /
   `generated-apply` review workflow.

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

zddv --project my_project ai-provider-request \
  --context .zddv/debug/ai-rca-context.json

zddv --project my_project ai-provider-run \
  --context .zddv/debug/ai-rca-context.json \
  --provider openai-compatible \
  --endpoint https://provider.example/v1/chat/completions \
  --model provider-model \
  --api-key-env PROVIDER_API_KEY \
  --allow-external \
  --expected-request-sha256 <reviewed-request-sha256>
```

For a local OpenAI-compatible server:

```bash
zddv --project my_project ai-provider-request \
  --context .zddv/debug/ai-rca-context.json

zddv --project my_project ai-provider-run \
  --context .zddv/debug/ai-rca-context.json \
  --provider openai-compatible \
  --endpoint http://127.0.0.1:8000/v1/chat/completions \
  --model local-model \
  --allow-external \
  --expected-request-sha256 <reviewed-request-sha256>
```

Even for loopback endpoints, the built-in `openai-compatible` adapter retains
the explicit external-transmission gate because invocation is a deliberate user
action and ZDDV does not silently contact model services. A separately registered
provider with `external_transmission = false` is treated as local-only.

## Provider-neutral response contract

Every model request carries a deterministic `response_contract` beside the
evidence context. The contract lists the exact required JSON fields and the exact
evidence references available in that context. The request explicitly forbids
Markdown fences or surrounding prose.

The same evidence-reference builder is consumed by `ai-response-ingest`, so
prompting and validation cannot silently drift apart. The OpenAI-compatible
adapter still treats returned bytes as raw/untrusted output; schema validation
and the separate human review gate remain mandatory before a proposal can move
toward generated-artifact staging.

## Request review boundary

The preview SHA is calculated over the canonical provider-neutral request,
including the response contract and complete evidence context. For providers
marked as external, ZDDV recomputes that SHA immediately before invocation and
refuses transmission if it differs from `--expected-request-sha256`.

This makes `--allow-external` an explicit permission and the SHA an exact
content confirmation. Local-only providers registered with
`external_transmission = false` do not require the external request SHA gate.

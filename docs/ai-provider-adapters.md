# AI model-provider adapter boundary

ZDDV's AI-assisted debugging path is intentionally split into separate trust
boundaries.

1. `zddv ai-rca-context` builds deterministic, provider-neutral evidence.
2. `zddv ai-provider-run` is an explicit invocation boundary. External
   transmission is refused unless the user supplies `--allow-external`.
3. Provider output is stored as `ai_provider_response_raw` and remains
   untrusted. It is not schema-validated in this milestone.
4. Raw provider output cannot automatically stage generated verification code,
   modify project sources, run commands, compile, or simulate.
5. The next milestone is schema-validated response ingestion plus a human-review
   gate before any generated assertion/test can enter the existing
   `generated-stage` workflow.

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

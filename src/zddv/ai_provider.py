from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from zddv.ai_contract import model_response_contract
from zddv.config import ProjectConfig


_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _project_path(project: ProjectConfig, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    root = project.root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path must remain inside the project root: {path}") from exc
    if path == root:
        raise ValueError("Path must identify a file inside the project root")
    return path


@dataclass(frozen=True)
class ProviderMetadata:
    name: str
    description: str
    external_transmission: bool
    response_schema_validated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "external_transmission": self.external_transmission,
            "response_schema_validated": self.response_schema_validated,
        }


class ModelProviderAdapter(Protocol):
    metadata: ProviderMetadata

    def invoke(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return raw provider output without treating it as trusted analysis."""


ProviderFactory = Callable[..., ModelProviderAdapter]
_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}
_PROVIDER_METADATA: dict[str, ProviderMetadata] = {}


def register_provider(
    name: str,
    factory: ProviderFactory,
    *,
    metadata: ProviderMetadata,
    replace: bool = False,
) -> None:
    normalized = str(name).strip().lower()
    if not normalized:
        raise ValueError("provider name must not be empty")
    if metadata.name != normalized:
        raise ValueError("provider metadata name must match the registry key")
    if normalized in _PROVIDER_FACTORIES and not replace:
        raise ValueError(f"provider already registered: {normalized}")
    _PROVIDER_FACTORIES[normalized] = factory
    _PROVIDER_METADATA[normalized] = metadata


def provider_metadata() -> list[dict[str, Any]]:
    return [
        _PROVIDER_METADATA[name].as_dict()
        for name in sorted(_PROVIDER_METADATA)
    ]


def create_provider(name: str, **kwargs: Any) -> ModelProviderAdapter:
    normalized = str(name).strip().lower()
    try:
        factory = _PROVIDER_FACTORIES[normalized]
    except KeyError as exc:
        available = ", ".join(sorted(_PROVIDER_FACTORIES)) or "(none)"
        raise ValueError(
            f"Unknown model provider '{normalized}'. Available: {available}"
        ) from exc
    return factory(**kwargs)


def load_ai_context(
    project: ProjectConfig,
    path: str | Path,
) -> dict[str, Any]:
    source = _project_path(project, path)
    if not source.is_file():
        raise FileNotFoundError(source)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI context is not valid JSON: {source}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("AI context root must be a JSON object")
    context = dict(payload)
    if context.get("analysis") != "ai_rca_context":
        raise ValueError("Input is not a ZDDV ai_rca_context bundle")

    policy = context.get("policy")
    provenance = context.get("provenance")
    if not isinstance(policy, Mapping) or not isinstance(provenance, Mapping):
        raise ValueError("AI context is missing policy/provenance metadata")
    if policy.get("review_required_before_external_use") is not True:
        raise ValueError("AI context lacks the required external-use review gate")
    evidence_sha = str(provenance.get("evidence_sha256") or "").lower()
    if not _SHA256_RE.fullmatch(evidence_sha):
        raise ValueError("AI context has an invalid evidence_sha256")
    evidence = context.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("AI context is missing evidence")
    calculated_sha = _canonical_sha256(evidence)
    if evidence_sha != calculated_sha:
        raise ValueError(
            "AI context evidence_sha256 does not match the canonical evidence payload"
        )
    return context


def build_model_request(context: Mapping[str, Any]) -> dict[str, Any]:
    prompt_contract = context.get("prompt_contract")
    if not isinstance(prompt_contract, list) or not all(
        isinstance(item, str) and item.strip() for item in prompt_contract
    ):
        raise ValueError("AI context prompt_contract must be a non-empty string list")

    return {
        "schema_version": 1,
        "task": "zddv_root_cause_analysis",
        "instructions": [
            "Return analysis only from the supplied ZDDV evidence bundle.",
            *prompt_contract,
            "Return one JSON object matching response_contract exactly; do not wrap it in Markdown.",
            "Treat this request as analysis only. Do not claim any generated code was reviewed, applied, compiled, or executed.",
        ],
        "response_contract": model_response_contract(),
        "context": dict(context),
    }


def invoke_provider(
    provider: ModelProviderAdapter,
    context: Mapping[str, Any],
    *,
    allow_external: bool,
) -> dict[str, Any]:
    metadata = provider.metadata
    if metadata.external_transmission and not allow_external:
        raise RuntimeError(
            "External model-provider transmission is disabled. "
            "Re-run with explicit --allow-external after reviewing the context bundle."
        )

    request_payload = build_model_request(context)
    request_sha256 = _canonical_sha256(request_payload)
    response = provider.invoke(request_payload)
    if not isinstance(response, Mapping):
        raise TypeError("model provider must return a mapping")

    return {
        "schema_version": 1,
        "analysis": "ai_provider_response_raw",
        "provider": metadata.as_dict(),
        "request_sha256": request_sha256,
        "context_evidence_sha256": context["provenance"]["evidence_sha256"],
        "response": dict(response),
        "policy": {
            "explicit_external_opt_in": bool(
                metadata.external_transmission and allow_external
            ),
            "untrusted_model_output": True,
            "response_schema_validated": False,
            "automatic_generated_artifact_staging": False,
            "automatic_command_execution": False,
            "human_review_required": True,
        },
        "semantics": (
            "This artifact contains raw, untrusted model-provider output. "
            "ZDDV has not schema-validated the response, accepted any hypothesis as fact, "
            "staged generated verification code, modified project sources, or executed commands."
        ),
    }


def write_provider_response(
    project: ProjectConfig,
    provider: ModelProviderAdapter,
    *,
    context_path: str | Path,
    allow_external: bool,
    output: str | Path = ".zddv/ai/provider-response.json",
) -> dict[str, Any]:
    context = load_ai_context(project, context_path)
    result = invoke_provider(
        provider,
        context,
        allow_external=allow_external,
    )
    destination = _project_path(project, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "path": str(destination)}


_OPENAI_COMPATIBLE_METADATA = ProviderMetadata(
    name="openai-compatible",
    description=(
        "Explicit HTTP adapter for OpenAI-compatible chat-completions endpoints. "
        "HTTPS is required except for loopback-local endpoints."
    ),
    external_transmission=True,
    response_schema_validated=False,
)


class OpenAICompatibleProvider:
    metadata = _OPENAI_COMPATIBLE_METADATA

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key_env: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.endpoint = self._validate_endpoint(endpoint)
        self.model = str(model).strip()
        if not self.model:
            raise ValueError("model must not be empty")
        self.api_key_env = (
            str(api_key_env).strip() if api_key_env is not None else None
        )
        if self.api_key_env == "":
            self.api_key_env = None
        self.timeout_s = float(timeout_s)
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")

    @staticmethod
    def _validate_endpoint(endpoint: str) -> str:
        value = str(endpoint).strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("provider endpoint must be an absolute http(s) URL")

        host = parsed.hostname.lower()
        loopback = host in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not loopback:
            raise ValueError(
                "Non-loopback model-provider endpoints must use HTTPS"
            )
        return value

    def _api_key(self) -> str | None:
        if self.api_key_env is None:
            return None
        value = os.environ.get(self.api_key_env)
        if not value:
            raise RuntimeError(
                f"Model-provider API key environment variable is not set: "
                f"{self.api_key_env}"
            )
        return value

    def invoke(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are assisting a hardware verification engineer. "
                        "Use only supplied evidence, separate facts from hypotheses, "
                        "and do not claim actions were executed."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        dict(request),
                        sort_keys=True,
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
        }
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key()
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key}"

        http_request = Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_s) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise RuntimeError(
                f"Model provider returned HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"Model provider connection failed: {exc.reason}"
            ) from exc

        if len(raw) > _MAX_RESPONSE_BYTES:
            raise RuntimeError(
                f"Model provider response exceeded {_MAX_RESPONSE_BYTES} bytes"
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Model provider returned invalid UTF-8 JSON") from exc
        if not isinstance(payload, Mapping):
            raise RuntimeError("Model provider JSON root is not an object")

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Model provider response is missing choices[0]")
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise RuntimeError("Model provider choices[0] is not an object")
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise RuntimeError(
                "Model provider choices[0].message is not an object"
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError(
                "Model provider choices[0].message.content is not a string"
            )

        return {
            "provider_response_id": payload.get("id"),
            "provider_model": payload.get("model"),
            "finish_reason": choice.get("finish_reason"),
            "content": content,
        }


register_provider(
    "openai-compatible",
    OpenAICompatibleProvider,
    metadata=_OPENAI_COMPATIBLE_METADATA,
)

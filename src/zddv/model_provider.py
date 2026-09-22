from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from zddv.config import ProjectConfig


ProviderFactory = Callable[..., "ModelProviderAdapter"]


class ModelProviderAdapter(Protocol):
    name: str

    def invoke(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return raw provider transport evidence without interpreting it."""


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_project_path(project: ProjectConfig, path: str | Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = project.root / resolved
    return resolved.resolve()


def _validate_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.netloc
    ):
        return
    raise ValueError(
        "provider endpoint must use HTTPS, except loopback localhost endpoints may use HTTP"
    )


def validate_ai_rca_context(context: dict[str, Any]) -> dict[str, Any]:
    if context.get("schema_version") != 1:
        raise ValueError("unsupported AI RCA context schema_version")
    if context.get("analysis") != "ai_rca_context":
        raise ValueError("input is not a ZDDV AI RCA context bundle")

    evidence = context.get("evidence")
    provenance = context.get("provenance")
    policy = context.get("policy")
    if not isinstance(evidence, dict):
        raise ValueError("AI RCA context is missing evidence")
    if not isinstance(provenance, dict):
        raise ValueError("AI RCA context is missing provenance")
    if not isinstance(policy, dict):
        raise ValueError("AI RCA context is missing policy")

    recorded_sha = provenance.get("evidence_sha256")
    calculated_sha = _canonical_sha256(evidence)
    if not isinstance(recorded_sha, str) or recorded_sha != calculated_sha:
        raise ValueError("AI RCA context evidence SHA-256 does not match its evidence payload")

    if policy.get("review_required_before_external_use") is not True:
        raise ValueError("AI RCA context does not require review before external use")

    return {
        "evidence_sha256": calculated_sha,
        "context_sha256": _canonical_sha256(context),
    }


def _http_post_json(
    endpoint: str,
    payload: dict[str, Any],
    *,
    api_key: str | None,
    timeout_seconds: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    _validate_endpoint(endpoint)
    body = json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "zddv-model-provider/1",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                raise RuntimeError(
                    f"provider response exceeded {max_response_bytes} byte safety limit"
                )
            text = raw.decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type")
            status = getattr(response, "status", None)
    except HTTPError as exc:
        body = exc.read(max_response_bytes + 1)
        if len(body) > max_response_bytes:
            body = body[:max_response_bytes]
        detail = body.decode("utf-8", errors="replace")
        raise RuntimeError(f"provider HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"provider connection failed: {exc.reason}") from exc

    parsed_json: Any = None
    try:
        parsed_json = json.loads(text)
    except json.JSONDecodeError:
        pass

    return {
        "http_status": status,
        "content_type": content_type,
        "body_text": text,
        "body_json": parsed_json,
    }


class HttpJsonModelProvider:
    name = "http-json"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        model: str | None = None,
        api_key_env: str = "ZDDV_MODEL_API_KEY",
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        if not endpoint:
            raise ValueError("http-json provider requires --endpoint")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if max_response_bytes < 1024:
            raise ValueError("max_response_bytes must be >= 1024")
        _validate_endpoint(endpoint)
        self.endpoint = endpoint
        self.model = model
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def invoke(self, context: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get(self.api_key_env) if self.api_key_env else None
        payload = {
            "schema_version": 1,
            "task": "zddv_root_cause_analysis",
            "model": self.model,
            "context": context,
            "response_contract": {
                "format": "json_preferred",
                "status": "raw_untrusted",
                "instruction": (
                    "Use only evidence in context. Separate facts, hypotheses, unknowns, "
                    "and proposed next checks. Do not invent tool results."
                ),
            },
        }
        response = _http_post_json(
            self.endpoint,
            payload,
            api_key=api_key,
            timeout_seconds=self.timeout_seconds,
            max_response_bytes=self.max_response_bytes,
        )
        return {
            "adapter": self.name,
            "endpoint": self.endpoint,
            "model": self.model,
            "request_sha256": _canonical_sha256(payload),
            "transport": response,
        }


_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
    HttpJsonModelProvider.name: HttpJsonModelProvider,
}


def register_model_provider_adapter(
    name: str,
    factory: ProviderFactory,
    *,
    replace: bool = False,
) -> None:
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("provider adapter name must not be empty")
    if normalized in _PROVIDER_FACTORIES and not replace:
        raise ValueError(f"provider adapter already registered: {normalized}")
    _PROVIDER_FACTORIES[normalized] = factory


def model_provider_adapter_names() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDER_FACTORIES))


def get_model_provider_adapter(name: str, **options: Any) -> ModelProviderAdapter:
    normalized = name.strip().lower()
    factory = _PROVIDER_FACTORIES.get(normalized)
    if factory is None:
        available = ", ".join(model_provider_adapter_names()) or "<none>"
        raise ValueError(
            f"unknown model provider adapter: {name}; available adapters: {available}"
        )
    adapter = factory(**options)
    if not hasattr(adapter, "invoke"):
        raise ValueError(f"provider adapter {normalized} does not implement invoke()")
    return adapter


def invoke_model_provider(
    project: ProjectConfig,
    *,
    context_path: str | Path = ".zddv/debug/ai-rca-context.json",
    provider: str = "http-json",
    expected_evidence_sha256: str,
    approve_external_transmission: bool,
    output: str | Path = ".zddv/debug/ai-provider-response.json",
    endpoint: str | None = None,
    model: str | None = None,
    api_key_env: str = "ZDDV_MODEL_API_KEY",
    timeout_seconds: float = 60.0,
    max_response_bytes: int = 2_000_000,
) -> dict[str, Any]:
    if not approve_external_transmission:
        raise ValueError(
            "external model invocation requires --approve-external-transmission"
        )

    context_file = _resolve_project_path(project, context_path)
    if not context_file.is_file():
        raise FileNotFoundError(context_file)
    context = json.loads(context_file.read_text(encoding="utf-8"))
    if not isinstance(context, dict):
        raise ValueError("AI RCA context JSON must contain an object")

    integrity = validate_ai_rca_context(context)
    if expected_evidence_sha256 != integrity["evidence_sha256"]:
        raise ValueError(
            "expected evidence SHA-256 does not match the reviewed AI RCA context"
        )

    options: dict[str, Any] = {}
    if provider.strip().lower() == HttpJsonModelProvider.name:
        options = {
            "endpoint": endpoint,
            "model": model,
            "api_key_env": api_key_env,
            "timeout_seconds": timeout_seconds,
            "max_response_bytes": max_response_bytes,
        }
    adapter = get_model_provider_adapter(provider, **options)
    raw_response = adapter.invoke(context)
    if not isinstance(raw_response, dict):
        raise ValueError("provider adapter invoke() must return a JSON-object-compatible dict")

    artifact = {
        "schema_version": 1,
        "analysis": "ai_provider_raw_response",
        "project": project.name,
        "provider": provider.strip().lower(),
        "context": {
            "path": str(context_file),
            "evidence_sha256": integrity["evidence_sha256"],
            "context_sha256": integrity["context_sha256"],
        },
        "policy": {
            "external_transmission_approved": True,
            "automatic_command_execution": False,
            "automatic_artifact_application": False,
            "response_trusted": False,
            "schema_validated": False,
            "human_review_required": True,
        },
        "semantics": (
            "This artifact stores raw provider output and transport evidence only. "
            "ZDDV has not schema-validated, trusted, executed, or applied any model proposal."
        ),
        "response": raw_response,
    }
    artifact["provenance"] = {
        "response_sha256": _canonical_sha256(raw_response),
        "artifact_payload_sha256": _canonical_sha256(artifact),
    }

    destination = _resolve_project_path(project, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**artifact, "path": str(destination)}

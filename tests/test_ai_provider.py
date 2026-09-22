from __future__ import annotations

from pathlib import Path

import pytest

from zddv.ai_provider import (
    ProviderMetadata,
    build_model_request,
    build_model_response_contract,
    create_provider,
    invoke_provider,
    load_ai_context,
    model_evidence_references,
    provider_metadata,
    register_provider,
    write_provider_response,
)
from zddv.config import initialize_project


def _context() -> dict:
    return {
        "schema_version": 1,
        "analysis": "ai_rca_context",
        "project": "demo",
        "prompt_contract": [
            "Use only evidence present in this bundle.",
            "Separate observed facts, hypotheses, unknowns, and proposed next checks.",
        ],
        "policy": {
            "provider_neutral": True,
            "external_transmission": False,
            "automatic_model_invocation": False,
            "automatic_command_execution": False,
            "review_required_before_external_use": True,
        },
        "provenance": {
            "evidence_sha256": "a" * 64,
            "deterministic": True,
        },
        "evidence": {"run": {"run_id": "run-fail"}},
    }


class _FakeExternalProvider:
    metadata = ProviderMetadata(
        name="test-external",
        description="test provider",
        external_transmission=True,
        response_schema_validated=False,
    )

    def invoke(self, request):
        return {"content": "candidate 1 is worth checking"}


class _FakeLocalProvider:
    metadata = ProviderMetadata(
        name="test-local",
        description="test local provider",
        external_transmission=False,
        response_schema_validated=False,
    )

    def invoke(self, request):
        return {"content": "local raw response"}


def test_model_request_preserves_context_and_contract():
    request = build_model_request(_context())

    assert request["task"] == "zddv_root_cause_analysis"
    assert request["context"]["provenance"]["evidence_sha256"] == "a" * 64
    assert "Use only evidence present in this bundle." in request["instructions"]
    assert any("analysis only" in item for item in request["instructions"])
    assert any("strict JSON object" in item for item in request["instructions"])
    assert any("Markdown fences" in item for item in request["instructions"])
    contract = request["response_contract"]
    assert contract["format"] == "strict-json-object"
    assert contract["markdown_fences_allowed"] is False
    assert contract["additional_properties_allowed"] is False
    assert contract["schema"]["analysis"] == "zddv_ai_rca_response"
    assert contract["allowed_evidence_refs"] == ["run:run-fail"]


def test_response_contract_uses_exact_context_evidence_vocabulary():
    context = _context()
    context["evidence"] = {
        "run": {"run_id": "run-fail"},
        "candidates": [{"rank": 2}, {"rank": 1}],
        "debug_probe_suggestions": [{"rank": 3}],
        "limitations": ["first", "second"],
    }

    refs = model_evidence_references(context)
    contract = build_model_response_contract(context)

    assert refs == [
        "candidate:1",
        "candidate:2",
        "limitation:1",
        "limitation:2",
        "probe:3",
        "run:run-fail",
    ]
    assert contract["allowed_evidence_refs"] == refs
    assert contract["required"] == [
        "schema_version",
        "analysis",
        "observed_facts",
        "hypotheses",
        "unknowns",
        "next_checks",
        "generated_proposals",
    ]
    assert any(
        "Unknown evidence references will be rejected." == item
        for item in contract["semantics"]
    )


def test_external_provider_requires_explicit_opt_in():
    provider = _FakeExternalProvider()

    with pytest.raises(RuntimeError, match="--allow-external"):
        invoke_provider(provider, _context(), allow_external=False)

    result = invoke_provider(provider, _context(), allow_external=True)
    assert result["analysis"] == "ai_provider_response_raw"
    assert result["policy"]["explicit_external_opt_in"] is True
    assert result["policy"]["untrusted_model_output"] is True
    assert result["policy"]["response_schema_validated"] is False
    assert result["policy"]["automatic_generated_artifact_staging"] is False
    assert result["policy"]["automatic_command_execution"] is False
    assert result["policy"]["human_review_required"] is True
    assert len(result["request_sha256"]) == 64


def test_local_provider_does_not_require_external_opt_in():
    result = invoke_provider(
        _FakeLocalProvider(),
        _context(),
        allow_external=False,
    )
    assert result["policy"]["explicit_external_opt_in"] is False
    assert result["response"]["content"] == "local raw response"


def test_context_loader_and_writer_stay_inside_project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context_path = project.root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True)
    import json

    context_path.write_text(json.dumps(_context()), encoding="utf-8")

    loaded = load_ai_context(project, context_path)
    assert loaded["analysis"] == "ai_rca_context"

    result = write_provider_response(
        project,
        _FakeLocalProvider(),
        context_path=context_path,
        allow_external=False,
    )
    assert Path(result["path"]).is_file()
    assert result["response"]["content"] == "local raw response"

    with pytest.raises(ValueError, match="inside the project root"):
        load_ai_context(project, tmp_path / "outside.json")


def test_registry_is_pluggable_and_builtin_openai_adapter_is_safe():
    name = "unit-test-provider"
    metadata = ProviderMetadata(
        name=name,
        description="unit test",
        external_transmission=False,
    )
    register_provider(name, lambda **kwargs: _FakeLocalProvider(), metadata=metadata)

    created = create_provider(name)
    assert created.invoke({})["content"] == "local raw response"
    assert name in {item["name"] for item in provider_metadata()}

    provider = create_provider(
        "openai-compatible",
        endpoint="http://127.0.0.1:8000/v1/chat/completions",
        model="local-model",
    )
    assert provider.metadata.external_transmission is True

    with pytest.raises(ValueError, match="must use HTTPS"):
        create_provider(
            "openai-compatible",
            endpoint="http://example.com/v1/chat/completions",
            model="remote-model",
        )

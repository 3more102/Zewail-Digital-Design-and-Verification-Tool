from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zddv.ai_provider import (
    ProviderMetadata,
    build_model_request,
    build_model_request_preview,
    build_model_response_contract,
    create_provider,
    import_provider_response,
    invoke_provider,
    load_ai_context,
    model_evidence_references,
    provider_metadata,
    register_provider,
    write_model_request_preview,
    write_provider_response,
)
from zddv.cli import main
from zddv.config import initialize_project


def _evidence_sha(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _context() -> dict:
    evidence = {"run": {"run_id": "run-fail"}}
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
            "evidence_sha256": _evidence_sha(evidence),
            "deterministic": True,
        },
        "evidence": evidence,
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
    assert (
        request["context"]["provenance"]["evidence_sha256"]
        == _context()["provenance"]["evidence_sha256"]
    )
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


def test_external_provider_requires_opt_in_and_exact_request_sha():
    provider = _FakeExternalProvider()
    preview = build_model_request_preview(_context())

    with pytest.raises(RuntimeError, match="--allow-external"):
        invoke_provider(
            provider,
            _context(),
            allow_external=False,
            expected_request_sha256=preview["request_sha256"],
        )

    with pytest.raises(RuntimeError, match="--expected-request-sha256"):
        invoke_provider(provider, _context(), allow_external=True)

    with pytest.raises(RuntimeError, match="does not match"):
        invoke_provider(
            provider,
            _context(),
            allow_external=True,
            expected_request_sha256="b" * 64,
        )

    result = invoke_provider(
        provider,
        _context(),
        allow_external=True,
        expected_request_sha256=preview["request_sha256"],
    )
    assert result["analysis"] == "ai_provider_response_raw"
    assert result["policy"]["explicit_external_opt_in"] is True
    assert result["policy"]["request_sha_confirmed"] is True
    assert result["policy"]["untrusted_model_output"] is True
    assert result["policy"]["response_schema_validated"] is False
    assert result["policy"]["automatic_generated_artifact_staging"] is False
    assert result["policy"]["automatic_command_execution"] is False
    assert result["policy"]["human_review_required"] is True
    assert result["request_sha256"] == preview["request_sha256"]


def test_local_provider_does_not_require_external_opt_in():
    result = invoke_provider(
        _FakeLocalProvider(),
        _context(),
        allow_external=False,
    )
    assert result["policy"]["explicit_external_opt_in"] is False
    assert result["policy"]["request_sha_confirmed"] is False
    assert result["response"]["content"] == "local raw response"


def test_request_preview_is_local_deterministic_and_writable(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context_path = project.root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True)
    context_path.write_text(json.dumps(_context()), encoding="utf-8")

    first = build_model_request_preview(_context())
    second = build_model_request_preview(_context())
    assert first["request_sha256"] == second["request_sha256"]
    assert first["policy"]["external_transmission"] is False
    assert first["policy"]["automatic_model_invocation"] is False
    assert first["request"] == build_model_request(_context())

    written = write_model_request_preview(
        project,
        context_path=context_path,
    )
    assert Path(written["path"]).is_file()
    assert written["request_sha256"] == first["request_sha256"]


def test_ai_provider_request_cli_writes_preview(
    tmp_path: Path,
    capsys,
):
    project = initialize_project(tmp_path / "demo")
    context_path = project.root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True)
    context_path.write_text(json.dumps(_context()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "ai-provider-request",
            "--context",
            ".zddv/debug/ai-rca-context.json",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "AI PROVIDER REQUEST PREVIEW: LOCAL ONLY" in output
    assert "Request SHA-256:" in output
    assert "External transmission: disabled" in output
    assert (project.root / ".zddv" / "ai" / "provider-request.json").is_file()


def test_manual_response_import_is_offline_untrusted_and_request_bound(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    context_path = project.root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True)
    context_path.write_text(json.dumps(_context()), encoding="utf-8")

    content_path = project.root / ".zddv" / "ai" / "manual-response.json"
    content_path.parent.mkdir(parents=True)
    content_path.write_text(
        '{"schema_version":1,"analysis":"zddv_ai_rca_response"}\n',
        encoding="utf-8",
    )

    result = import_provider_response(
        project,
        context_path=context_path,
        content_path=content_path,
        provider_label="offline-review",
    )

    assert Path(result["path"]).is_file()
    assert result["analysis"] == "ai_provider_response_raw"
    assert result["provider"]["name"] == "offline-review"
    assert result["provider"]["external_transmission"] is False
    assert result["response"]["content"] == content_path.read_text(encoding="utf-8")
    assert len(result["request_sha256"]) == 64
    assert result["policy"]["explicit_external_opt_in"] is False
    assert result["policy"]["request_sha_confirmed"] is False
    assert result["policy"]["untrusted_model_output"] is True
    assert result["policy"]["response_schema_validated"] is False
    assert result["policy"]["automatic_generated_artifact_staging"] is False
    assert result["policy"]["automatic_command_execution"] is False
    assert result["policy"]["human_review_required"] is True
    assert result["provenance"]["provider_invocation_performed"] is False
    assert (
        result["provenance"]["imported_content_sha256"]
        == hashlib.sha256(content_path.read_bytes()).hexdigest()
    )
    preview = build_model_request_preview(_context())
    assert result["request_sha256"] == preview["request_sha256"]


def test_manual_response_import_rejects_external_content_path(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context_path = project.root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True)
    context_path.write_text(json.dumps(_context()), encoding="utf-8")
    outside = tmp_path / "outside-response.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the project root"):
        import_provider_response(
            project,
            context_path=context_path,
            content_path=outside,
        )


def test_context_loader_and_writer_stay_inside_project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context_path = project.root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True)
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


def test_context_loader_rejects_evidence_changed_after_digest(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context = _context()
    context["evidence"]["run"]["run_id"] = "tampered-run"
    context_path = project.root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True)
    context_path.write_text(json.dumps(context), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match the canonical evidence payload"):
        load_ai_context(project, context_path)


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

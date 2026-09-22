from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.model_provider import (
    get_model_provider_adapter,
    invoke_model_provider,
    model_provider_adapter_names,
    register_model_provider_adapter,
    validate_ai_rca_context,
)


def _sha(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _context() -> dict:
    evidence = {
        "run": {"run_id": "run-fail", "status": "FAIL"},
        "failure_signature": "assertion:count_guard",
        "candidates": [
            {
                "rank": 1,
                "kind": "rtl_driver",
                "subject": "rtl/counter.sv:8 count",
                "evidence_score": 95,
            }
        ],
        "limitations": [],
        "debug_probe_suggestions": [],
        "debug_probe_blockers": [],
    }
    return {
        "schema_version": 1,
        "analysis": "ai_rca_context",
        "project": "demo",
        "policy": {
            "provider_neutral": True,
            "external_transmission": False,
            "automatic_model_invocation": False,
            "automatic_command_execution": False,
            "review_required_before_external_use": True,
        },
        "provenance": {
            "evidence_sha256": _sha(evidence),
            "deterministic": True,
        },
        "evidence": evidence,
    }


def _write_context(root: Path) -> tuple[Path, dict]:
    payload = _context()
    path = root / ".zddv" / "debug" / "ai-rca-context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload


def test_context_integrity_validation_rejects_tampering():
    context = _context()
    integrity = validate_ai_rca_context(context)
    assert integrity["evidence_sha256"] == context["provenance"]["evidence_sha256"]
    assert len(integrity["context_sha256"]) == 64

    context["evidence"]["failure_signature"] = "tampered"
    with pytest.raises(ValueError, match="evidence SHA-256"):
        validate_ai_rca_context(context)


def test_provider_registry_accepts_custom_adapter():
    class FixtureAdapter:
        name = "fixture-provider"

        def invoke(self, context: dict) -> dict:
            return {"ok": True, "run": context["evidence"]["run"]["run_id"]}

    register_model_provider_adapter(
        "fixture-provider",
        lambda: FixtureAdapter(),
        replace=True,
    )
    assert "fixture-provider" in model_provider_adapter_names()
    adapter = get_model_provider_adapter("fixture-provider")
    assert adapter.invoke(_context())["run"] == "run-fail"


def test_invocation_requires_explicit_opt_in_and_exact_reviewed_sha(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _, context = _write_context(project.root)

    class FixtureAdapter:
        name = "review-gate-fixture"

        def invoke(self, context: dict) -> dict:
            return {"answer": "raw only"}

    register_model_provider_adapter(
        "review-gate-fixture",
        lambda: FixtureAdapter(),
        replace=True,
    )
    evidence_sha = context["provenance"]["evidence_sha256"]

    with pytest.raises(ValueError, match="approve-external-transmission"):
        invoke_model_provider(
            project,
            provider="review-gate-fixture",
            expected_evidence_sha256=evidence_sha,
            approve_external_transmission=False,
        )

    with pytest.raises(ValueError, match="reviewed AI RCA context"):
        invoke_model_provider(
            project,
            provider="review-gate-fixture",
            expected_evidence_sha256="0" * 64,
            approve_external_transmission=True,
        )

    result = invoke_model_provider(
        project,
        provider="review-gate-fixture",
        expected_evidence_sha256=evidence_sha,
        approve_external_transmission=True,
    )
    assert result["analysis"] == "ai_provider_raw_response"
    assert result["policy"]["response_trusted"] is False
    assert result["policy"]["schema_validated"] is False
    assert result["policy"]["human_review_required"] is True
    assert result["response"] == {"answer": "raw only"}
    assert len(result["provenance"]["response_sha256"]) == 64
    assert Path(result["path"]).is_file()


def test_http_json_adapter_passes_context_without_exposing_api_key(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    _, context = _write_context(project.root)
    evidence_sha = context["provenance"]["evidence_sha256"]
    captured: dict = {}

    def fake_post(endpoint, payload, *, api_key, timeout_seconds, max_response_bytes):
        captured.update(
            {
                "endpoint": endpoint,
                "payload": payload,
                "api_key": api_key,
                "timeout_seconds": timeout_seconds,
                "max_response_bytes": max_response_bytes,
            }
        )
        return {
            "http_status": 200,
            "content_type": "application/json",
            "body_text": '{"hypotheses":[]}',
            "body_json": {"hypotheses": []},
        }

    monkeypatch.setattr("zddv.model_provider._http_post_json", fake_post)
    monkeypatch.setenv("TEST_ZDDV_KEY", "secret-token")

    result = invoke_model_provider(
        project,
        provider="http-json",
        endpoint="https://provider.example/v1/zddv",
        model="debug-model",
        api_key_env="TEST_ZDDV_KEY",
        expected_evidence_sha256=evidence_sha,
        approve_external_transmission=True,
        timeout_seconds=12.0,
    )

    assert captured["api_key"] == "secret-token"
    assert captured["payload"]["context"]["evidence"] == context["evidence"]
    assert captured["payload"]["model"] == "debug-model"
    serialized = json.dumps(result)
    assert "secret-token" not in serialized
    assert result["response"]["transport"]["http_status"] == 200


def test_http_json_adapter_rejects_cleartext_remote_endpoint():
    with pytest.raises(ValueError, match="HTTPS"):
        get_model_provider_adapter(
            "http-json",
            endpoint="http://provider.example/v1/zddv",
        )

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zddv.ai_provider import ProviderMetadata, invoke_provider
from zddv.ai_response import (
    export_reviewed_generation_proposal,
    ingest_ai_provider_response,
    review_validated_ai_response,
)
from zddv.config import initialize_project
from zddv.generated_artifacts import stage_generated_artifact


class _FakeProvider:
    metadata = ProviderMetadata(
        name="test-local",
        description="test",
        external_transmission=False,
        response_schema_validated=False,
    )

    def __init__(self, content: str):
        self.content = content

    def invoke(self, request):
        return {"content": self.content}


def _evidence_sha(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _context() -> dict:
    evidence = {
        "run": {"run_id": "run-fail"},
        "failure_signature": "assertion:count_guard",
        "candidates": [
            {
                "rank": 1,
                "kind": "rtl_driver",
                "subject": "rtl/counter.sv:8 count",
            }
        ],
        "limitations": ["waveform truncated"],
        "debug_probe_suggestions": [
            {
                "rank": 1,
                "signal": "TOP.tb_top.dut.count",
            }
        ],
        "debug_probe_blockers": [],
    }
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


def _model_payload() -> dict:
    return {
        "schema_version": 1,
        "analysis": "zddv_ai_rca_response",
        "observed_facts": [
            {
                "statement": "Candidate 1 is an explicit RTL driver.",
                "evidence_refs": ["candidate:1"],
            }
        ],
        "hypotheses": [
            {
                "statement": "The driver should be inspected around the failing event.",
                "evidence_refs": ["candidate:1", "run:run-fail"],
            }
        ],
        "unknowns": ["The exact bad value is not established by this bundle."],
        "next_checks": [
            {
                "description": "Probe the candidate signal.",
                "evidence_refs": ["probe:1"],
            }
        ],
        "generated_proposals": [
            {
                "kind": "assertion",
                "language": "systemverilog",
                "name": "count_guard_followup",
                "content": (
                    "assert property (@(posedge clk) disable iff (!rst_n) "
                    "count <= 8'hff);\n"
                ),
                "target_path": "tb/generated/count_guard_followup.sv",
                "evidence_refs": ["candidate:1"],
            }
        ],
    }


def _write_context_and_raw(project, *, payload=None):
    context = _context()
    context_path = project.root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(
        json.dumps(context, indent=2),
        encoding="utf-8",
    )

    model_payload = payload if payload is not None else _model_payload()
    raw = invoke_provider(
        _FakeProvider(json.dumps(model_payload)),
        context,
        allow_external=False,
    )
    response_path = project.root / ".zddv" / "ai" / "provider-response.json"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(
        json.dumps(raw, indent=2),
        encoding="utf-8",
    )
    return context_path, response_path


def test_ingest_validates_schema_and_evidence_references(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context_path, response_path = _write_context_and_raw(project)

    result = ingest_ai_provider_response(
        project,
        response_path=response_path,
        context_path=context_path,
    )

    assert result["status"] == "VALIDATED_UNREVIEWED"
    assert result["policy"]["schema_validated"] is True
    assert result["policy"]["approved"] is False
    assert result["policy"]["automatic_generated_artifact_staging"] is False
    assert len(result["validated_payload_sha256"]) == 64
    assert (
        result["validated_payload"]["generated_proposals"][0]["name"]
        == "count_guard_followup"
    )


def test_ingest_rejects_unknown_evidence_reference(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    payload = _model_payload()
    payload["hypotheses"][0]["evidence_refs"] = ["candidate:99"]
    context_path, response_path = _write_context_and_raw(
        project,
        payload=payload,
    )

    with pytest.raises(ValueError, match="unknown evidence reference"):
        ingest_ai_provider_response(
            project,
            response_path=response_path,
            context_path=context_path,
        )


def test_ingest_rejects_free_form_or_markdown_model_output(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context = _context()
    context_path = project.root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(json.dumps(context), encoding="utf-8")
    raw = invoke_provider(
        _FakeProvider("Here is my answer: fenced JSON"),
        context,
        allow_external=False,
    )
    response_path = project.root / ".zddv" / "ai" / "provider-response.json"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="strict JSON object"):
        ingest_ai_provider_response(
            project,
            response_path=response_path,
            context_path=context_path,
        )


def test_review_requires_explicit_flag_and_exact_sha(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context_path, response_path = _write_context_and_raw(project)
    validated = ingest_ai_provider_response(
        project,
        response_path=response_path,
        context_path=context_path,
    )

    with pytest.raises(RuntimeError, match="--approve-reviewed"):
        review_validated_ai_response(
            project,
            validated["path"],
            expected_sha256=validated["validated_payload_sha256"],
            approve_reviewed=False,
        )

    with pytest.raises(RuntimeError, match="does not match"):
        review_validated_ai_response(
            project,
            validated["path"],
            expected_sha256="b" * 64,
            approve_reviewed=True,
        )

    review = review_validated_ai_response(
        project,
        validated["path"],
        expected_sha256=validated["validated_payload_sha256"],
        approve_reviewed=True,
    )
    assert review["status"] == "APPROVED"
    assert review["approved"] is True
    assert review["policy"]["automatic_generated_artifact_staging"] is False


def test_reviewed_proposal_exports_into_existing_staging_flow(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context_path, response_path = _write_context_and_raw(project)
    validated = ingest_ai_provider_response(
        project,
        response_path=response_path,
        context_path=context_path,
    )
    review = review_validated_ai_response(
        project,
        validated["path"],
        expected_sha256=validated["validated_payload_sha256"],
        approve_reviewed=True,
    )

    exported = export_reviewed_generation_proposal(
        project,
        review["path"],
        proposal_index=1,
    )
    assert exported["status"] == "EXPORTED_FOR_STAGING"
    proposal_path = Path(exported["path"])
    assert proposal_path.is_file()
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert proposal["source"] == "ai-reviewed-response"
    assert proposal["evidence"]["review_id"] == review["review_id"]

    staged = stage_generated_artifact(project, proposal_path)
    assert staged["status"] == "DRAFT"
    assert staged["review_required"] is True
    assert staged["auto_apply"] is False
    assert staged["execution_enabled"] is False

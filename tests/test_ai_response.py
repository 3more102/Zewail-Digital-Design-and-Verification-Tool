from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zddv.ai_response import ingest_provider_response, validate_model_response
from zddv.config import initialize_project


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
        "limitations": ["waveform missing after 120 ns"],
        "debug_probe_suggestions": [
            {
                "rank": 1,
                "kind": "waveform_probe",
                "signal": "TOP.tb_top.dut.count",
                "source_candidate_rank": 1,
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
            "evidence_sha256": _sha(evidence),
            "deterministic": True,
        },
        "evidence": evidence,
    }


def _model_response(context: dict) -> dict:
    return {
        "schema_version": 1,
        "analysis": "zddv_ai_rca_response",
        "context_evidence_sha256": context["provenance"]["evidence_sha256"],
        "hypotheses": [
            {
                "id": "H1",
                "summary": "The explicit RTL driver is the first evidence-backed inspection point.",
                "evidence_refs": [
                    {"kind": "candidate", "rank": 1},
                    {"kind": "debug_probe", "rank": 1},
                    {"kind": "failure_signature"},
                ],
                "unknowns": ["No waveform evidence exists after 120 ns."],
                "next_checks": ["Run the already-suggested waveform probe."],
            }
        ],
        "overall_unknowns": ["Causality is not established by ranking alone."],
        "next_checks": ["Review H1 against the waveform probe result."],
    }


def _write_inputs(project_root: Path) -> tuple[Path, Path, dict]:
    context = _context()
    context_path = project_root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(json.dumps(context), encoding="utf-8")

    raw = {
        "schema_version": 1,
        "analysis": "ai_provider_response_raw",
        "provider": {
            "name": "test-local",
            "external_transmission": False,
            "response_schema_validated": False,
        },
        "request_sha256": "b" * 64,
        "context_evidence_sha256": context["provenance"]["evidence_sha256"],
        "response": {
            "content": json.dumps(_model_response(context)),
        },
        "policy": {
            "untrusted_model_output": True,
            "response_schema_validated": False,
            "human_review_required": True,
        },
    }
    raw_path = project_root / ".zddv" / "ai" / "provider-response.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    return context_path, raw_path, context


def test_validate_model_response_resolves_explicit_evidence_refs():
    context = _context()
    result = validate_model_response(_model_response(context), context=context)

    assert result["summary"]["hypotheses"] == 1
    assert result["summary"]["resolved_evidence_refs"] == 3
    resolved = result["hypotheses"][0]["resolved_evidence"]
    assert resolved[0]["kind"] == "candidate"
    assert resolved[0]["subject"] == "rtl/counter.sv:8 count"
    assert resolved[1]["signal"] == "TOP.tb_top.dut.count"
    assert resolved[2]["value"] == "assertion:count_guard"


def test_validate_model_response_rejects_unresolvable_reference():
    context = _context()
    payload = _model_response(context)
    payload["hypotheses"][0]["evidence_refs"] = [{"kind": "candidate", "rank": 99}]

    with pytest.raises(ValueError, match="candidate rank does not exist"):
        validate_model_response(payload, context=context)


def test_validate_model_response_rejects_evidence_sha_mismatch():
    context = _context()
    payload = _model_response(context)
    payload["context_evidence_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="different evidence SHA-256"):
        validate_model_response(payload, context=context)


def test_ingest_provider_response_writes_validated_untrusted_artifact(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context_path, raw_path, context = _write_inputs(project.root)

    result = ingest_provider_response(
        project,
        raw_response_path=raw_path,
        context_path=context_path,
    )

    assert result["analysis"] == "ai_provider_response_validated"
    assert result["context_evidence_sha256"] == context["provenance"]["evidence_sha256"]
    assert result["policy"]["response_schema_validated"] is True
    assert result["policy"]["evidence_references_resolved"] is True
    assert result["policy"]["model_output_remains_untrusted"] is True
    assert result["policy"]["automatic_generated_artifact_staging"] is False
    assert result["policy"]["automatic_command_execution"] is False
    assert result["policy"]["human_review_required"] is True
    assert len(result["provenance"]["validated_payload_sha256"]) == 64
    assert Path(result["path"]).is_file()


def test_ingest_provider_response_rejects_wrong_context_link(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context_path, raw_path, _ = _write_inputs(project.root)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["context_evidence_sha256"] = "f" * 64
    raw_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        ingest_provider_response(
            project,
            raw_response_path=raw_path,
            context_path=context_path,
        )

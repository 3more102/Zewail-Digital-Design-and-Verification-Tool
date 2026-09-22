from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zddv.ai_audit import audit_ai_chain
from zddv.ai_provider import ProviderMetadata, invoke_provider
from zddv.ai_response import (
    ingest_ai_provider_response,
    review_validated_ai_response,
)
from zddv.cli import main
from zddv.config import initialize_project


class _FakeProvider:
    metadata = ProviderMetadata(
        name="test-local",
        description="test local provider",
        external_transmission=False,
        response_schema_validated=False,
    )

    def invoke(self, request):
        return {"content": json.dumps(_model_payload())}


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
        "candidates": [
            {
                "rank": 1,
                "kind": "rtl_driver",
                "subject": "rtl/counter.sv:8 count",
            }
        ],
        "limitations": ["waveform truncated"],
        "debug_probe_suggestions": [
            {"rank": 1, "signal": "TOP.tb_top.dut.count"}
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
                "statement": "Inspect the driver near the failing event.",
                "evidence_refs": ["candidate:1", "run:run-fail"],
            }
        ],
        "unknowns": ["The exact bad value is not established."],
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
                "content": "assert property (@(posedge clk) count <= 8'hff);\n",
                "target_path": "tb/generated/count_guard_followup.sv",
                "evidence_refs": ["candidate:1"],
            }
        ],
    }


def _write_chain(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    context = _context()
    context_path = project.root / ".zddv" / "debug" / "ai-rca-context.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(json.dumps(context, indent=2), encoding="utf-8")

    raw = invoke_provider(_FakeProvider(), context, allow_external=False)
    response_path = project.root / ".zddv" / "ai" / "provider-response.json"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

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
    return project, context_path, response_path, validated, review


def test_ai_chain_audit_verifies_through_human_review(tmp_path: Path):
    project, context_path, response_path, validated, review = _write_chain(tmp_path)

    result = audit_ai_chain(
        project,
        context_path=context_path,
        response_path=response_path,
        validated_path=validated["path"],
        review_path=review["path"],
    )

    assert result["status"] == "PASS"
    assert result["last_verified_stage"] == "human-review"
    assert [stage["stage"] for stage in result["stages"]] == [
        "context",
        "provider-response",
        "validated-response",
        "human-review",
    ]
    assert result["chain"]["review_id"] == review["review_id"]
    assert result["policy"]["read_only_validation"] is True
    assert result["policy"]["model_invocation"] is False
    assert Path(result["path"]).is_file()


def test_ai_chain_audit_can_stop_at_validated_response(tmp_path: Path):
    project, context_path, response_path, validated, _review = _write_chain(tmp_path)

    result = audit_ai_chain(
        project,
        context_path=context_path,
        response_path=response_path,
        validated_path=validated["path"],
    )

    assert result["status"] == "PASS"
    assert result["last_verified_stage"] == "validated-response"
    assert result["chain"]["review_id"] is None


def test_ai_chain_audit_rejects_raw_response_changed_after_validation(
    tmp_path: Path,
):
    project, context_path, response_path, validated, review = _write_chain(tmp_path)
    raw = json.loads(response_path.read_text(encoding="utf-8"))
    raw["response"]["content"] = json.dumps(
        {
            **_model_payload(),
            "unknowns": ["tampered after validation"],
        }
    )
    response_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    with pytest.raises(RuntimeError, match="raw-response SHA-256"):
        audit_ai_chain(
            project,
            context_path=context_path,
            response_path=response_path,
            validated_path=validated["path"],
            review_path=review["path"],
        )


def test_ai_chain_audit_refuses_output_outside_audit_root(tmp_path: Path):
    project, context_path, response_path, validated, _review = _write_chain(tmp_path)

    with pytest.raises(ValueError, match="must remain under .zddv/ai/audits"):
        audit_ai_chain(
            project,
            context_path=context_path,
            response_path=response_path,
            validated_path=validated["path"],
            output=".zddv/ai/not-an-audit.json",
        )


def test_cli_ai_chain_audit(tmp_path: Path, capsys):
    project, context_path, response_path, validated, review = _write_chain(tmp_path)

    rc = main(
        [
            "--project",
            str(project.root),
            "ai-chain-audit",
            "--context",
            str(context_path),
            "--response",
            str(response_path),
            "--validated",
            str(validated["path"]),
            "--review",
            str(review["path"]),
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "AI CHAIN AUDIT: PASS through=human-review" in output
    assert "Model invocation: disabled" in output
    assert "External transmission: disabled" in output
    assert "Command execution: disabled" in output

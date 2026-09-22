from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.generated_artifacts import (
    apply_generated_artifact,
    stage_generated_artifact,
)


def _proposal(project, *, target_path: str = "reviewed_generated/p_req_ack.sv") -> Path:
    path = project.root / "proposal.json"
    payload = {
        "kind": "assertion",
        "language": "systemverilog",
        "name": "p_req_ack_review_example",
        "target_path": target_path,
        "source": "unit-review",
        "evidence": {"coverage_hole": "formal.req_ack"},
        "content": (
            "module zddv_generated_review_example(\n"
            "    input logic clk,\n"
            "    input logic rst_n,\n"
            "    input logic req,\n"
            "    input logic ack\n"
            ");\n"
            "    p_req_ack_review_example: assert property (\n"
            "        @(posedge clk) disable iff (!rst_n) req |-> ##[1:2] ack\n"
            "    );\n"
            "endmodule\n"
        ),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def test_stage_generated_artifact_is_review_only(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    proposal = _proposal(project)

    result = stage_generated_artifact(project, proposal)

    assert result["status"] == "DRAFT"
    assert result["review_required"] is True
    assert result["approved"] is False
    assert result["auto_apply"] is False
    assert result["execution_enabled"] is False
    assert result["suggested_target_path"] == "reviewed_generated/p_req_ack.sv"
    assert not (project.root / result["suggested_target_path"]).exists()

    content_path = Path(result["content_path"])
    assert content_path.is_file()
    content = content_path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == result["content_sha256"]

    manifest_path = Path(result["manifest_path"])
    assert manifest_path.is_file()
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["content_sha256"] == result["content_sha256"]
    assert persisted["status"] == "DRAFT"


def test_apply_requires_explicit_approval_and_reviewed_sha(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    manifest = staged["manifest_path"]

    with pytest.raises(RuntimeError, match="approve-reviewed"):
        apply_generated_artifact(
            project,
            manifest,
            destination=None,
            expected_sha256=staged["content_sha256"],
            approve_reviewed=False,
        )

    wrong_sha = "0" * 64
    assert wrong_sha != staged["content_sha256"]
    with pytest.raises(RuntimeError, match="does not match"):
        apply_generated_artifact(
            project,
            manifest,
            destination=None,
            expected_sha256=wrong_sha,
            approve_reviewed=True,
        )

    result = apply_generated_artifact(
        project,
        manifest,
        destination=None,
        expected_sha256=staged["content_sha256"],
        approve_reviewed=True,
    )

    destination = project.root / "reviewed_generated" / "p_req_ack.sv"
    assert result["status"] == "APPLIED"
    assert result["approved"] is True
    assert result["auto_apply"] is False
    assert result["execution_enabled"] is False
    assert result["destination"] == str(destination.resolve())
    assert destination.read_bytes() == Path(staged["content_path"]).read_bytes()
    assert Path(result["applied_record_path"]).is_file()


def test_apply_refuses_project_escape_and_existing_file(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))

    with pytest.raises(ValueError, match="inside the project root"):
        apply_generated_artifact(
            project,
            staged["manifest_path"],
            destination="../escape.sv",
            expected_sha256=staged["content_sha256"],
            approve_reviewed=True,
        )

    existing = project.root / "tb" / "existing.sv"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("module existing; endmodule\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        apply_generated_artifact(
            project,
            staged["manifest_path"],
            destination="tb/existing.sv",
            expected_sha256=staged["content_sha256"],
            approve_reviewed=True,
        )


def test_apply_rejects_tampered_draft(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    Path(staged["content_path"]).write_text(
        "module tampered; endmodule\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="changed after staging"):
        apply_generated_artifact(
            project,
            staged["manifest_path"],
            destination=None,
            expected_sha256=staged["content_sha256"],
            approve_reviewed=True,
        )


def test_generation_proposal_rejects_unsupported_kind(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    proposal = project.root / "proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "kind": "script",
                "language": "systemverilog",
                "name": "bad",
                "content": "module bad; endmodule\n",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="kind must be"):
        stage_generated_artifact(project, proposal)


def test_generated_artifact_cli_stage_and_apply(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    _proposal(project)

    rc = main(
        [
            "--project",
            str(project.root),
            "generated-stage",
            "proposal.json",
        ]
    )
    assert rc == 0
    stage_output = capsys.readouterr().out
    assert "GENERATED DRAFT:" in stage_output
    assert "Project source modified: no" in stage_output

    manifests = list(
        (project.root / ".zddv" / "generated" / "drafts").glob("*/manifest.json")
    )
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["approved"] is False
    assert not (project.root / "reviewed_generated" / "p_req_ack.sv").exists()

    rc = main(
        [
            "--project",
            str(project.root),
            "generated-apply",
            str(manifests[0]),
            "--expected-sha256",
            manifest["content_sha256"],
            "--approve-reviewed",
        ]
    )
    assert rc == 0
    apply_output = capsys.readouterr().out
    assert "GENERATED ARTIFACT APPLIED:" in apply_output
    assert "Execution: disabled" in apply_output
    assert (project.root / "reviewed_generated" / "p_req_ack.sv").is_file()

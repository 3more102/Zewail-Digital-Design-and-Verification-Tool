from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.desktop_generated_review import (
    apply_reviewed_generated_draft,
    list_reviewable_generated_drafts,
)
from zddv.generated_artifacts import stage_generated_artifact


def _proposal(project) -> Path:
    path = project.root / "proposal.json"
    path.write_text(
        json.dumps(
            {
                "kind": "assertion",
                "language": "systemverilog",
                "name": "p_desktop_review",
                "target_path": "reviewed_generated/p_desktop_review.sv",
                "source": "desktop-action-test",
                "evidence": {"assertion": "p_desktop_review"},
                "content": "module p_desktop_review; endmodule\n",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_generated_review_lists_intact_draft_read_only(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    destination = project.root / "reviewed_generated" / "p_desktop_review.sv"

    rows = list_reviewable_generated_drafts(project)

    assert len(rows) == 1
    row = rows[0]
    assert row["draft_id"] == staged["draft_id"]
    assert row["status"] == "DRAFT"
    assert row["integrity"] == "MATCH"
    assert row["reviewable"] is True
    assert row["already_applied"] is False
    assert row["execution_enabled"] is False
    assert row["content_sha256"] == staged["content_sha256"]
    assert row["content"] == "module p_desktop_review; endmodule\n"
    assert not destination.exists()


def test_generated_review_preserves_core_approval_and_sha_gates(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))

    with pytest.raises(RuntimeError, match="approve-reviewed"):
        apply_reviewed_generated_draft(
            project,
            manifest_path=staged["manifest_path"],
            destination=None,
            reviewed_sha256=staged["content_sha256"],
            approve_reviewed=False,
        )

    with pytest.raises(RuntimeError, match="does not match"):
        apply_reviewed_generated_draft(
            project,
            manifest_path=staged["manifest_path"],
            destination=None,
            reviewed_sha256="0" * 64,
            approve_reviewed=True,
        )

    result = apply_reviewed_generated_draft(
        project,
        manifest_path=staged["manifest_path"],
        destination=None,
        reviewed_sha256=staged["content_sha256"],
        approve_reviewed=True,
    )

    destination = project.root / "reviewed_generated" / "p_desktop_review.sv"
    assert result["status"] == "APPLIED"
    assert result["execution_enabled"] is False
    assert destination.read_text(encoding="utf-8") == "module p_desktop_review; endmodule\n"

    row = list_reviewable_generated_drafts(project)[0]
    assert row["status"] == "APPLIED"
    assert row["already_applied"] is True
    assert row["reviewable"] is False


def test_generated_review_rejects_tampered_staged_content(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    Path(staged["content_path"]).write_text(
        "module tampered; endmodule\n",
        encoding="utf-8",
    )

    row = list_reviewable_generated_drafts(project)[0]

    assert row["integrity"] == "MISMATCH"
    assert row["reviewable"] is False


def test_generated_review_rejects_execution_enabled_manifest(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    manifest = Path(staged["manifest_path"])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["execution_enabled"] = True
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    row = list_reviewable_generated_drafts(project)[0]

    assert row["reviewable"] is False


def test_generated_review_rejects_content_path_escape(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    manifest = Path(staged["manifest_path"])
    outside = project.root / ".zddv" / "generated" / "outside.sv"
    outside.write_text("module outside; endmodule\n", encoding="utf-8")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["content_path"] = str(outside)
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    row = list_reviewable_generated_drafts(project)[0]

    assert row["reviewable"] is False
    assert "outside its draft directory" in str(row["error"])

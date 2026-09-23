from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.desktop_actions import (
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


def test_desktop_action_lists_intact_staged_draft_without_project_mutation(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    destination = project.root / "reviewed_generated" / "p_desktop_review.sv"
    assert not destination.exists()

    rows = list_reviewable_generated_drafts(project)

    assert len(rows) == 1
    row = rows[0]
    assert row["draft_id"] == staged["draft_id"]
    assert row["status"] == "DRAFT"
    assert row["integrity"] == "MATCH"
    assert row["reviewable"] is True
    assert row["already_applied"] is False
    assert row["content_sha256"] == staged["content_sha256"]
    assert row["suggested_target_path"] == "reviewed_generated/p_desktop_review.sv"
    assert row["content"] == "module p_desktop_review; endmodule\n"
    assert not destination.exists()


def test_desktop_action_preserves_core_approval_and_sha_gates(tmp_path: Path):
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

    wrong_sha = "0" * 64
    assert wrong_sha != staged["content_sha256"]
    with pytest.raises(RuntimeError, match="does not match"):
        apply_reviewed_generated_draft(
            project,
            manifest_path=staged["manifest_path"],
            destination=None,
            reviewed_sha256=wrong_sha,
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

    rows = list_reviewable_generated_drafts(project)
    assert rows[0]["status"] == "APPLIED"
    assert rows[0]["already_applied"] is True
    assert rows[0]["reviewable"] is False


def test_desktop_action_rejects_tampered_staged_content_before_apply(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    Path(staged["content_path"]).write_text(
        "module tampered; endmodule\n",
        encoding="utf-8",
    )

    row = list_reviewable_generated_drafts(project)[0]

    assert row["integrity"] == "MISMATCH"
    assert row["reviewable"] is False

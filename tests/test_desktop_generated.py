from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.desktop_generated import (
    apply_reviewed_generated_draft,
    list_generated_draft_reviews,
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
                "source": "desktop-generated-test",
                "evidence": {"assertion": "p_desktop_review"},
                "content": "module p_desktop_review; endmodule\n",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_generated_review_lists_intact_draft_without_project_mutation(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    destination = project.root / "reviewed_generated" / "p_desktop_review.sv"
    assert not destination.exists()

    rows = list_generated_draft_reviews(project)

    assert len(rows) == 1
    row = rows[0]
    assert row["draft_id"] == staged["draft_id"]
    assert row["status"] == "DRAFT"
    assert row["integrity"] == "MATCH"
    assert row["reviewable"] is True
    assert row["already_applied"] is False
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


def test_generated_review_applies_exact_bytes_once(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))

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
    assert destination.read_text(encoding="utf-8") == (
        "module p_desktop_review; endmodule\n"
    )

    row = list_generated_draft_reviews(project)[0]
    assert row["status"] == "APPLIED"
    assert row["already_applied"] is True
    assert row["reviewable"] is False


def test_generated_review_detects_tampered_staged_content(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    Path(staged["content_path"]).write_text(
        "module tampered; endmodule\n",
        encoding="utf-8",
    )

    row = list_generated_draft_reviews(project)[0]
    assert row["integrity"] == "MISMATCH"
    assert row["reviewable"] is False
    assert "does not match" in row["error"]

    with pytest.raises(RuntimeError, match="changed after staging"):
        apply_reviewed_generated_draft(
            project,
            manifest_path=staged["manifest_path"],
            destination=None,
            reviewed_sha256=staged["content_sha256"],
            approve_reviewed=True,
        )

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.desktop_actions import (
    apply_desktop_generated_draft,
    list_desktop_generated_drafts,
    read_desktop_generated_draft,
)
from zddv.generated_artifacts import stage_generated_artifact


def _proposal(project, *, target: str = "reviewed_generated/p_ready.sv") -> Path:
    path = project.root / "proposal.json"
    path.write_text(
        json.dumps(
            {
                "kind": "assertion",
                "language": "systemverilog",
                "name": "p_ready",
                "target_path": target,
                "source": "desktop-action-test",
                "evidence": {"reason": "explicit operator review"},
                "content": (
                    "module reviewed_assertion(input logic clk, ready);\n"
                    "  p_ready: assert property (@(posedge clk) ready);\n"
                    "endmodule\n"
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_desktop_generated_draft_listing_and_preview_are_read_only(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    destination = project.root / "reviewed_generated" / "p_ready.sv"
    assert not destination.exists()

    rows = list_desktop_generated_drafts(project)

    assert len(rows) == 1
    row = rows[0]
    assert row["draft_id"] == staged["draft_id"]
    assert row["status"] == "DRAFT"
    assert row["integrity"] == "MATCH"
    assert row["actual_sha256"] == staged["content_sha256"]
    assert row["eligible"] is True
    assert row["applied"] is False

    reviewed = read_desktop_generated_draft(project, staged["manifest_path"])
    assert reviewed["content_sha256"] == staged["content_sha256"]
    assert "p_ready: assert property" in reviewed["content"]
    assert not destination.exists()


def test_desktop_apply_requires_phrase_and_exact_reviewed_sha(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    destination = project.root / "reviewed_generated" / "p_ready.sv"

    with pytest.raises(RuntimeError, match="APPLY REVIEWED"):
        apply_desktop_generated_draft(
            project,
            staged["manifest_path"],
            reviewed_sha256=staged["content_sha256"],
            approval_phrase="APPLY",
        )
    assert not destination.exists()

    with pytest.raises(RuntimeError, match="does not match"):
        apply_desktop_generated_draft(
            project,
            staged["manifest_path"],
            reviewed_sha256="0" * 64,
            approval_phrase="APPLY REVIEWED",
        )
    assert not destination.exists()

    result = apply_desktop_generated_draft(
        project,
        staged["manifest_path"],
        reviewed_sha256=staged["content_sha256"],
        approval_phrase="APPLY REVIEWED",
    )

    assert result["status"] == "APPLIED"
    assert result["approved"] is True
    assert result["execution_enabled"] is False
    assert destination.is_file()

    rows = list_desktop_generated_drafts(project)
    assert rows[0]["status"] == "APPLIED"
    assert rows[0]["eligible"] is False
    assert rows[0]["applied"] is True


def test_desktop_review_refuses_tampered_staged_content(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_generated_artifact(project, _proposal(project))
    Path(staged["content_path"]).write_text(
        "module tampered; endmodule\n",
        encoding="utf-8",
    )

    rows = list_desktop_generated_drafts(project)
    assert rows[0]["integrity"] == "MISMATCH"
    assert rows[0]["eligible"] is False

    with pytest.raises(RuntimeError, match="no longer matches"):
        read_desktop_generated_draft(project, staged["manifest_path"])

    with pytest.raises(RuntimeError, match="no longer matches"):
        apply_desktop_generated_draft(
            project,
            staged["manifest_path"],
            reviewed_sha256=staged["content_sha256"],
            approval_phrase="APPLY REVIEWED",
        )


def test_desktop_review_refuses_manifest_outside_registered_drafts(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    fake = project.root / "fake-manifest.json"
    fake.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not registered"):
        read_desktop_generated_draft(project, fake)

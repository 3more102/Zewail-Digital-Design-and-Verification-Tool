from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.desktop_review import (
    apply_desktop_reviewed_draft,
    list_desktop_review_drafts,
    load_desktop_review_draft,
)
from zddv.generated_artifacts import stage_generated_artifact


def _stage(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    proposal = project.root / "proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "kind": "assertion",
                "language": "systemverilog",
                "name": "ready_stable",
                "content": "assert property (@(posedge clk) valid |-> ready);",
                "target_path": "verification/ready_stable.sv",
                "source": "desktop-review-test",
                "evidence": {"reason": "unit-test"},
            }
        ),
        encoding="utf-8",
    )
    staged = stage_generated_artifact(project, proposal)
    return project, staged


def test_desktop_review_lists_and_loads_exact_staged_content(tmp_path: Path):
    project, staged = _stage(tmp_path)

    rows = list_desktop_review_drafts(project)
    assert len(rows) == 1
    row = rows[0]
    assert row["draft_id"] == staged["draft_id"]
    assert row["status"] == "REVIEWABLE"
    assert row["content_sha256"] == staged["content_sha256"]
    assert row["suggested_target_path"] == "verification/ready_stable.sv"

    detail = load_desktop_review_draft(project, row["manifest_path"])
    assert detail["content_sha256"] == staged["content_sha256"]
    assert detail["content"] == (
        "assert property (@(posedge clk) valid |-> ready);\n"
    )
    assert not (project.root / "verification" / "ready_stable.sv").exists()


def test_desktop_review_apply_requires_approval_and_exact_sha(tmp_path: Path):
    project, staged = _stage(tmp_path)
    manifest = staged["manifest_path"]
    target = project.root / "verification" / "ready_stable.sv"

    with pytest.raises(RuntimeError, match="explicit reviewed approval"):
        apply_desktop_reviewed_draft(
            project,
            manifest,
            confirmation_sha256=staged["content_sha256"],
            approved=False,
        )
    assert not target.exists()

    wrong = "0" * 64
    assert wrong != staged["content_sha256"]
    with pytest.raises(RuntimeError, match="does not match"):
        apply_desktop_reviewed_draft(
            project,
            manifest,
            confirmation_sha256=wrong,
            approved=True,
        )
    assert not target.exists()

    result = apply_desktop_reviewed_draft(
        project,
        manifest,
        confirmation_sha256=staged["content_sha256"],
        approved=True,
    )

    assert result["status"] == "APPLIED"
    assert result["approved"] is True
    assert result["auto_apply"] is False
    assert result["execution_enabled"] is False
    assert target.read_text(encoding="utf-8") == (
        "assert property (@(posedge clk) valid |-> ready);\n"
    )
    assert list_desktop_review_drafts(project)[0]["status"] == "APPLIED"


def test_desktop_review_blocks_tampered_staged_content(tmp_path: Path):
    project, staged = _stage(tmp_path)
    Path(staged["content_path"]).write_text(
        "assert property (@(posedge clk) 1'b0);\n",
        encoding="utf-8",
    )

    row = list_desktop_review_drafts(project)[0]
    assert row["status"] == "INVALID"
    assert "changed after staging" in row["error"]

    with pytest.raises(RuntimeError, match="changed after staging"):
        apply_desktop_reviewed_draft(
            project,
            staged["manifest_path"],
            confirmation_sha256=staged["content_sha256"],
            approved=True,
        )


def test_desktop_review_limit_must_be_positive(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    with pytest.raises(ValueError, match="limit must be >= 1"):
        list_desktop_review_drafts(project, limit=0)

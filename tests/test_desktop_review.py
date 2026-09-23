from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.desktop_review import (
    apply_desktop_review_draft,
    inspect_desktop_review_draft,
    list_desktop_review_drafts,
    stage_desktop_review_proposal,
)


def _proposal(project, *, name: str = "p_desktop_review") -> Path:
    path = project.root / "proposal.json"
    path.write_text(
        json.dumps(
            {
                "kind": "assertion",
                "language": "systemverilog",
                "name": name,
                "target_path": f"reviewed_generated/{name}.sv",
                "source": "desktop-review-test",
                "evidence": {"reason": "explicit test proposal"},
                "content": (
                    "module desktop_review_example(input logic clk);\n"
                    f"  {name}: assert property (@(posedge clk) 1'b1);\n"
                    "endmodule\n"
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_desktop_review_stage_list_preview_and_apply_use_core_gates(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_desktop_review_proposal(project, _proposal(project))

    rows = list_desktop_review_drafts(project)
    assert len(rows) == 1
    assert rows[0]["draft_id"] == staged["draft_id"]
    assert rows[0]["status"] == "VALID"
    assert rows[0]["integrity"] == "VALID"
    assert rows[0]["applied"] is False
    assert rows[0]["content_sha256"] == staged["content_sha256"]

    detail = inspect_desktop_review_draft(
        project,
        staged["manifest_path"],
        include_content=True,
    )
    assert detail["actual_sha256"] == staged["content_sha256"]
    assert "assert property" in detail["content"]
    assert detail["content_truncated"] is False

    with pytest.raises(RuntimeError, match="approve-reviewed"):
        apply_desktop_review_draft(
            project,
            staged["manifest_path"],
            destination=None,
            expected_sha256=staged["content_sha256"],
            approve_reviewed=False,
        )

    with pytest.raises(RuntimeError, match="Reviewed SHA-256"):
        apply_desktop_review_draft(
            project,
            staged["manifest_path"],
            destination=None,
            expected_sha256="0" * 64,
            approve_reviewed=True,
        )

    result = apply_desktop_review_draft(
        project,
        staged["manifest_path"],
        destination=None,
        expected_sha256=staged["content_sha256"],
        approve_reviewed=True,
    )

    target = Path(result["destination"])
    assert target.is_file()
    assert target.read_bytes() == Path(staged["content_path"]).read_bytes()
    assert result["execution_enabled"] is False

    rows_after = list_desktop_review_drafts(project)
    assert rows_after[0]["status"] == "APPLIED"
    assert rows_after[0]["applied"] is True
    assert Path(rows_after[0]["applied_record_path"]).is_file()

    with pytest.raises(RuntimeError, match="already has an applied provenance record"):
        apply_desktop_review_draft(
            project,
            staged["manifest_path"],
            destination="reviewed_generated/second.sv",
            expected_sha256=staged["content_sha256"],
            approve_reviewed=True,
        )


def test_desktop_review_detects_changed_staged_bytes(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    staged = stage_desktop_review_proposal(project, _proposal(project))
    Path(staged["content_path"]).write_text(
        "module changed; endmodule\n",
        encoding="utf-8",
    )

    detail = inspect_desktop_review_draft(project, staged["manifest_path"])
    assert detail["status"] == "CHANGED"
    assert detail["integrity"] == "CHANGED"
    assert "content_sha256" in detail["error"]

    with pytest.raises(RuntimeError, match="not reviewable: CHANGED"):
        apply_desktop_review_draft(
            project,
            staged["manifest_path"],
            destination=None,
            expected_sha256=staged["content_sha256"],
            approve_reviewed=True,
        )


def test_desktop_review_rejects_manifest_outside_generated_drafts(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    outside = project.root / "manifest.json"
    outside.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must remain under"):
        inspect_desktop_review_draft(project, outside)


def test_desktop_review_limit_is_validated(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    with pytest.raises(ValueError, match="limit must be >= 1"):
        list_desktop_review_drafts(project, limit=0)

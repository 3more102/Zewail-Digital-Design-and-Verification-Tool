from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.desktop_actions import (
    apply_desktop_reviewed_artifact,
    list_desktop_generated_drafts,
    read_desktop_generated_draft,
    stage_desktop_generated_proposal,
)


def _proposal(project_root: Path) -> Path:
    path = project_root / "proposal.json"
    path.write_text(
        """{
  "kind": "assertion",
  "language": "systemverilog",
  "name": "ready_stable",
  "content": "property p_ready_stable; @(posedge clk) ready |=> ready; endproperty\nassert property (p_ready_stable);\n",
  "target_path": "tb/generated/ready_stable.sv",
  "source": "desktop-test",
  "evidence": {"run_id": "run-1"}
}
""",
        encoding="utf-8",
    )
    return path


def test_desktop_review_actions_require_exact_sha_and_explicit_review(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    proposal = _proposal(project.root)

    staged = stage_desktop_generated_proposal(project, proposal)

    target = project.root / "tb" / "generated" / "ready_stable.sv"
    assert not target.exists()

    rows = list_desktop_generated_drafts(project)
    assert len(rows) == 1
    assert rows[0]["draft_id"] == staged["draft_id"]
    assert rows[0]["status"] == "DRAFT"
    assert rows[0]["content_sha256"] == staged["content_sha256"]

    detail = read_desktop_generated_draft(project, rows[0]["manifest_path"])
    assert detail["content_sha256"] == staged["content_sha256"]
    assert "assert property (p_ready_stable);" in detail["content"]

    with pytest.raises(RuntimeError, match="explicit confirmation"):
        apply_desktop_reviewed_artifact(
            project,
            rows[0]["manifest_path"],
            expected_sha256=staged["content_sha256"],
            reviewed=False,
        )
    assert not target.exists()

    with pytest.raises(RuntimeError, match="Reviewed SHA-256 does not match"):
        apply_desktop_reviewed_artifact(
            project,
            rows[0]["manifest_path"],
            expected_sha256="0" * 64,
            reviewed=True,
        )
    assert not target.exists()

    applied = apply_desktop_reviewed_artifact(
        project,
        rows[0]["manifest_path"],
        expected_sha256=staged["content_sha256"],
        reviewed=True,
    )

    assert applied["status"] == "APPLIED"
    assert Path(applied["destination"]) == target
    assert target.read_text(encoding="utf-8") == detail["content"]

    after = list_desktop_generated_drafts(project)
    assert after[0]["status"] == "APPLIED"
    assert Path(after[0]["applied_record_path"]).is_file()


def test_desktop_review_reader_rejects_manifest_outside_draft_root(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    fake = project.root / "manifest.json"
    fake.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="accepts only"):
        read_desktop_generated_draft(project, fake)


def test_desktop_review_list_is_bounded(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    with pytest.raises(ValueError, match="limit must be >= 1"):
        list_desktop_generated_drafts(project, limit=0)

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.review_templates import (
    build_reviewable_test_template,
    write_reviewable_test_template,
)


def _suggestions() -> dict:
    return {
        "suggestion_count": 2,
        "suggestions": [
            {
                "rank": 2,
                "kind": "coverage_test_intent",
                "coverage_type": "toggle",
                "hole_name": "tb_top.dut.ready",
                "intent": "Exercise the explicitly uncovered 1->0 transition.",
                "evidence": {
                    "signal": "tb_top.dut.ready",
                    "toggle_transition": "1->0",
                },
                "review_required": True,
                "auto_execute": False,
            },
            {
                "rank": 1,
                "kind": "coverage_test_intent",
                "coverage_type": "fsm",
                "hole_name": "rtl/dut.sv:fsm0:transition:IDLE->BUSY",
                "intent": "Target explicit FSM transition IDLE -> BUSY.",
                "evidence": {
                    "source_file": "rtl/dut.sv",
                    "line": 71,
                    "fsm_id": "fsm0",
                    "transition": "IDLE -> BUSY",
                },
                "review_required": True,
                "auto_execute": False,
            },
        ],
    }


def test_build_reviewable_test_template_is_inert_and_deterministic():
    result = build_reviewable_test_template(_suggestions())

    assert result["candidate_count"] == 2
    assert result["review_required"] is True
    assert result["auto_execute"] is False
    assert result["default_enabled"] is False
    assert [item["source_rank"] for item in result["candidates"]] == [1, 2]

    template = result["template"]
    assert "package zddv_generated_coverage_tests;" in template
    assert "task automatic zddv_cov_candidate_001();" in template
    assert "IDLE -> BUSY" in template
    assert "1->0" in template
    assert "initial begin" not in template
    assert "assert property" not in template


def test_build_reviewable_test_template_rejects_unsafe_flags():
    report = _suggestions()
    report["suggestions"][0]["auto_execute"] = True

    with pytest.raises(ValueError, match="non-executing"):
        build_reviewable_test_template(report)


def test_writer_requires_non_compiled_template_extension(tmp_path: Path):
    source = tmp_path / "suggestions.json"
    source.write_text(json.dumps(_suggestions()), encoding="utf-8")

    with pytest.raises(ValueError, match=r"\.svt extension"):
        write_reviewable_test_template(source, tmp_path / "generated.sv")


def test_coverage_template_cli_is_dry_run_by_default(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    source = project.root / ".zddv" / "coverage" / "test-suggestions.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(_suggestions()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "coverage-template",
            "--show",
            "1",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "COVERAGE TEST TEMPLATE PREVIEW: 2 candidate(s)" in output
    assert "DRY RUN: no template file written" in output
    assert not (
        project.root / ".zddv" / "generated" / "coverage-tests.svt"
    ).exists()


def test_coverage_template_cli_materializes_only_on_explicit_opt_in(
    tmp_path: Path,
    capsys,
):
    project = initialize_project(tmp_path / "demo")
    source = project.root / ".zddv" / "coverage" / "test-suggestions.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(_suggestions()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "coverage-template",
            "--limit",
            "1",
            "--materialize",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "MATERIALIZED REVIEW TEMPLATE: 1 candidate(s)" in output
    destination = project.root / ".zddv" / "generated" / "coverage-tests.svt"
    assert destination.is_file()
    template = destination.read_text(encoding="utf-8")
    assert "zddv_cov_candidate_001" in template
    assert "IDLE -> BUSY" in template
    assert "tb_top.dut.ready" not in template

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.coverage_templates import (
    build_coverage_review_templates,
    write_coverage_review_templates,
)


def _suggestions() -> dict:
    return {
        "source_total_holes": 2,
        "source_reported_holes": 2,
        "suggestion_count": 2,
        "suggestions": [
            {
                "rank": 2,
                "kind": "coverage_test_intent",
                "coverage_type": "toggle",
                "hole_name": "tb_top.dut.ready",
                "intent": "Exercise the explicitly uncovered 1->0 transition.",
                "evidence": {
                    "source_file": "rtl/dut.sv",
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
                "intent": "Reach the explicit IDLE -> BUSY transition.",
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


def test_build_coverage_review_templates_is_deterministic_and_inert():
    result = build_coverage_review_templates(_suggestions(), kind="both", limit=1)

    assert result["selected_suggestion_count"] == 1
    assert result["template_count"] == 2
    assert result["review_required"] is True
    assert result["auto_execute"] is False
    assert [item["kind"] for item in result["templates"]] == [
        "test",
        "assertion",
    ]
    assert all(
        item["source_suggestion_rank"] == 1 for item in result["templates"]
    )

    for item in result["templates"]:
        lines = item["content"].splitlines()
        assert lines
        assert all(not line or line.startswith("//") for line in lines)
        assert "review_required=true" in item["content"]
        assert "auto_execute=false" in item["content"]
        assert "IDLE -> BUSY" in item["content"]


def test_build_coverage_review_templates_rejects_executable_suggestion():
    report = _suggestions()
    report["suggestions"][0]["auto_execute"] = True

    with pytest.raises(ValueError, match="auto_execute=false"):
        build_coverage_review_templates(report)


def test_write_coverage_review_templates_refuses_overwrite_without_force(
    tmp_path: Path,
):
    source = tmp_path / "suggestions.json"
    source.write_text(json.dumps(_suggestions()), encoding="utf-8")
    output_dir = tmp_path / "generated"

    first = write_coverage_review_templates(
        source,
        output_dir,
        kind="test",
        limit=1,
    )
    assert Path(first["manifest_path"]).is_file()
    template_path = output_dir / first["templates"][0]["relative_path"]
    assert template_path.is_file()
    original = template_path.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        write_coverage_review_templates(
            source,
            output_dir,
            kind="test",
            limit=1,
        )

    second = write_coverage_review_templates(
        source,
        output_dir,
        kind="test",
        limit=1,
        force=True,
    )
    assert second["template_count"] == 1
    assert template_path.read_text(encoding="utf-8") == original


def test_coverage_generate_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    source = project.root / ".zddv" / "coverage" / "test-suggestions.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(_suggestions()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "coverage-generate",
            "--kind",
            "both",
            "--limit",
            "1",
            "--show",
            "2",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "COVERAGE REVIEW TEMPLATES: 2" in output
    generated = project.root / ".zddv" / "coverage" / "generated"
    manifest = json.loads((generated / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selected_suggestion_count"] == 1
    assert manifest["template_count"] == 2
    assert manifest["review_required"] is True
    assert manifest["auto_execute"] is False
    assert all(
        (generated / item["relative_path"]).is_file()
        for item in manifest["templates"]
    )

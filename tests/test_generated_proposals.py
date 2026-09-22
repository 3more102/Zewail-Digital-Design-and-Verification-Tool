from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.generated_proposals import (
    build_reviewable_verification_proposals,
    write_reviewable_verification_proposals,
)


def _suggestions() -> dict:
    return {
        "source_total_holes": 2,
        "source_reported_holes": 2,
        "suggestion_count": 2,
        "suggestions": [
            {
                "rank": 1,
                "kind": "coverage_test_intent",
                "coverage_type": "fsm",
                "hole_name": "dut:fsm0:IDLE->BUSY",
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
            {
                "rank": 2,
                "kind": "coverage_test_intent",
                "coverage_type": "toggle",
                "hole_name": "tb.dut.ready",
                "intent": "Exercise the explicitly uncovered 1->0 transition.",
                "evidence": {
                    "signal": "tb.dut.ready",
                    "toggle_transition": "1->0",
                },
                "review_required": True,
                "auto_execute": False,
            },
        ],
    }


def test_build_proposals_are_review_only_and_do_not_invent_semantics():
    result = build_reviewable_verification_proposals(_suggestions())

    assert result["proposal_count"] == 2
    assert result["policy"]["review_required"] is True
    assert result["policy"]["automatic_source_modification"] is False
    assert result["policy"]["automatic_execution"] is False
    assert result["policy"]["code_emission_default"] is False

    first = result["proposals"][0]
    assert first["proposal_id"] == "coverage-0001"
    assert first["evidence"]["transition"] == "IDLE -> BUSY"
    assert first["review_required"] is True
    assert first["auto_apply"] is False
    assert "TODO(review)" in first["test_scaffold"]
    assert "Intentionally no executable SystemVerilog" in first["test_scaffold"]
    assert "TODO clock" in first["assertion_scaffold"]
    assert "// assert property" in first["assertion_scaffold"]
    assert "posedge clk" not in first["assertion_scaffold"]


def test_write_bundle_does_not_emit_code_without_explicit_opt_in(tmp_path: Path):
    source = tmp_path / "test-suggestions.json"
    output = tmp_path / "review-bundle.json"
    source.write_text(json.dumps(_suggestions()), encoding="utf-8")

    result = write_reviewable_verification_proposals(source, output)

    assert output.is_file()
    assert result["emission_opt_in"] is False
    assert result["emitted_artifacts"] == []
    assert not list(tmp_path.rglob("*.sv.disabled"))


def test_explicit_emit_dir_writes_disabled_review_scaffolds(tmp_path: Path):
    source = tmp_path / "test-suggestions.json"
    output = tmp_path / "review-bundle.json"
    emit_dir = tmp_path / "generated"
    source.write_text(json.dumps(_suggestions()), encoding="utf-8")

    result = write_reviewable_verification_proposals(
        source,
        output,
        emit_dir=emit_dir,
        limit=1,
    )

    assert result["emission_opt_in"] is True
    assert result["proposal_count"] == 1
    assert len(result["emitted_artifacts"]) == 2
    paths = [Path(item["path"]) for item in result["emitted_artifacts"]]
    assert all(path.is_file() for path in paths)
    assert all(path.name.endswith(".sv.disabled") for path in paths)
    assert any("-test.sv.disabled" in path.name for path in paths)
    assert any("-assertion.sv.disabled" in path.name for path in paths)
    assert all("REVIEW REQUIRED" in path.read_text(encoding="utf-8") for path in paths)


def test_emit_refuses_overwrite_without_explicit_force(tmp_path: Path):
    source = tmp_path / "test-suggestions.json"
    output = tmp_path / "review-bundle.json"
    emit_dir = tmp_path / "generated"
    source.write_text(json.dumps(_suggestions()), encoding="utf-8")

    first = write_reviewable_verification_proposals(
        source,
        output,
        emit_dir=emit_dir,
        limit=1,
    )
    edited = Path(first["emitted_artifacts"][0]["path"])
    edited.write_text("// engineer review edit\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--force"):
        write_reviewable_verification_proposals(
            source,
            output,
            emit_dir=emit_dir,
            limit=1,
        )

    assert edited.read_text(encoding="utf-8") == "// engineer review edit\n"

    forced = write_reviewable_verification_proposals(
        source,
        output,
        emit_dir=emit_dir,
        limit=1,
        force=True,
    )
    assert forced["emission_opt_in"] is True
    assert "REVIEW REQUIRED" in edited.read_text(encoding="utf-8")


def test_invalid_limit_is_rejected():
    with pytest.raises(ValueError, match="limit must be >= 1"):
        build_reviewable_verification_proposals(_suggestions(), limit=0)


def test_cli_requires_explicit_emit_opt_in(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    source = project.root / ".zddv" / "coverage" / "test-suggestions.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(_suggestions()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "verification-proposals",
            "--limit",
            "1",
            "--show",
            "1",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "VERIFICATION PROPOSALS: 1 review-required proposal(s); emitted=0" in output
    assert "Automatic source modification: disabled" in output
    assert "Automatic execution: disabled" in output
    assert "use --emit-dir to opt in" in output
    assert not list(project.root.rglob("*.sv.disabled"))
    report = (
        project.root / ".zddv" / "debug" / "verification-proposals.json"
    )
    assert report.is_file()


def test_cli_emit_dir_is_explicit_and_disabled(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    source = project.root / ".zddv" / "coverage" / "test-suggestions.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(_suggestions()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "verification-proposals",
            "--limit",
            "1",
            "--emit-dir",
            ".zddv/generated/review",
        ]
    )

    assert rc == 0
    capsys.readouterr()
    emitted = list((project.root / ".zddv" / "generated" / "review").glob("*.sv.disabled"))
    assert len(emitted) == 2
    assert all("REVIEW REQUIRED" in path.read_text(encoding="utf-8") for path in emitted)

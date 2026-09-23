from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import initialize_project, save_project
from zddv.desktop_actions import (
    execute_desktop_action,
    prepare_desktop_action,
)


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "rtl" / "top.sv"
    source.write_text(
        """module top;
logic count;
always_comb count = 1'b0;
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    project.simulator = "verilator"
    save_project(project)
    return project, source


def _lint_result(project):
    return {
        "status": "PASS",
        "returncode": 0,
        "errors": 0,
        "warnings": 0,
        "log": str(project.root / ".zddv" / "lint" / "lint.log"),
        "summary": str(project.root / ".zddv" / "lint" / "lint.json"),
    }


def test_prepare_desktop_action_is_review_only_and_source_bound(tmp_path: Path):
    project, source = _project(tmp_path)

    first = prepare_desktop_action(project, "lint")
    second = prepare_desktop_action(project, "lint")

    assert first == second
    assert first["action"] == "lint"
    assert first["core_api"] == "zddv.lint.lint_project"
    assert len(first["review_sha256"]) == 64
    assert first["review_policy"] == {
        "requires_exact_sha256": True,
        "requires_explicit_approval": True,
        "revalidates_project_before_execution": True,
    }
    assert first["project"]["sources"][0]["path"] == "rtl/top.sv"
    assert len(first["project"]["sources"][0]["sha256"]) == 64
    assert first["project"]["sources"][0]["bytes"] == source.stat().st_size
    assert not (project.root / ".zddv" / "lint").exists()


def test_execute_desktop_action_requires_exact_sha_and_explicit_approval(
    tmp_path: Path,
    monkeypatch,
):
    project, _ = _project(tmp_path)
    proposal = prepare_desktop_action(project, "lint")
    calls = []

    def fake_lint(project_arg):
        calls.append(project_arg.root)
        return _lint_result(project_arg)

    monkeypatch.setattr("zddv.desktop_actions.lint_project", fake_lint)

    with pytest.raises(RuntimeError, match="approval"):
        execute_desktop_action(
            project,
            proposal,
            expected_sha256=proposal["review_sha256"],
            approve_reviewed=False,
        )

    with pytest.raises(RuntimeError, match="Expected SHA-256"):
        execute_desktop_action(
            project,
            proposal,
            expected_sha256="0" * 64,
            approve_reviewed=True,
        )

    result = execute_desktop_action(
        project,
        proposal,
        expected_sha256=proposal["review_sha256"],
        approve_reviewed=True,
    )

    assert calls == [project.root]
    assert result["action"] == "lint"
    assert result["status"] == "PASS"
    assert result["review_sha256"] == proposal["review_sha256"]


def test_execute_desktop_action_rejects_source_drift_after_review(
    tmp_path: Path,
    monkeypatch,
):
    project, source = _project(tmp_path)
    proposal = prepare_desktop_action(project, "lint")
    called = False

    def fake_lint(_project_arg):
        nonlocal called
        called = True
        return _lint_result(_project_arg)

    monkeypatch.setattr("zddv.desktop_actions.lint_project", fake_lint)
    source.write_text(
        """module top;
logic count;
always_comb count = 1'b1;
endmodule
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="changed after review"):
        execute_desktop_action(
            project,
            proposal,
            expected_sha256=proposal["review_sha256"],
            approve_reviewed=True,
        )

    assert called is False


def test_execute_desktop_action_rejects_tampered_review_payload(
    tmp_path: Path,
    monkeypatch,
):
    project, _ = _project(tmp_path)
    proposal = prepare_desktop_action(project, "lint")
    proposal["effects"] = ["tampered"]
    called = False

    def fake_lint(_project_arg):
        nonlocal called
        called = True
        return _lint_result(_project_arg)

    monkeypatch.setattr("zddv.desktop_actions.lint_project", fake_lint)

    with pytest.raises(RuntimeError, match="does not match its review SHA-256"):
        execute_desktop_action(
            project,
            proposal,
            expected_sha256=proposal["review_sha256"],
            approve_reviewed=True,
        )

    assert called is False


def test_run_review_hash_binds_runtime_parameters(tmp_path: Path):
    project, _ = _project(tmp_path)

    base = prepare_desktop_action(
        project,
        "run",
        test_name="smoke",
        seed=42,
        plusargs=["+MODE=stress", "+COUNT=4"],
        timeout_s=30.0,
    )
    changed = prepare_desktop_action(
        project,
        "run",
        test_name="smoke",
        seed=43,
        plusargs=["+MODE=stress", "+COUNT=4"],
        timeout_s=30.0,
    )

    assert base["parameters"] == {
        "test_name": "smoke",
        "seed": 42,
        "plusargs": ["+MODE=stress", "+COUNT=4"],
        "timeout_s": 30.0,
    }
    assert base["review_sha256"] != changed["review_sha256"]


def test_non_run_action_rejects_hidden_runtime_parameters(tmp_path: Path):
    project, _ = _project(tmp_path)

    with pytest.raises(ValueError, match="only valid for the run action"):
        prepare_desktop_action(project, "build", seed=7)

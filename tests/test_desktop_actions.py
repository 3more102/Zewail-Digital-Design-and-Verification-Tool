from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import initialize_project, save_project
from zddv.desktop_actions import (
    execute_desktop_action,
    prepare_desktop_action,
)
from zddv.storage import record_run


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


def _record_historical_run(project, *, run_id: str = "run-fail", seed: int = 7) -> None:
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": "2026-09-23T07:20:00+00:00",
            "project": project.name,
            "simulator": project.simulator,
            "simulator_version": "Verilator test",
            "top": project.top,
            "test": "smoke",
            "seed": seed,
            "status": "FAIL",
            "returncode": 1,
            "duration_ms": 10.0,
            "run_dir": f".zddv/runs/{run_id}",
            "log": f".zddv/runs/{run_id}/simulation.log",
            "waveform": None,
            "coverage": None,
            "timeout_s": 25.0,
            "command": ["sim", "+MODE=stress"],
            "plusargs": ["+MODE=stress", "+COUNT=4"],
        },
    )


def test_rerun_review_binds_historical_record(tmp_path: Path):
    project, _ = _project(tmp_path)
    _record_historical_run(project)

    proposal = prepare_desktop_action(project, "rerun", run_id="run-fail")

    assert proposal["parameters"] == {"run_id": "run-fail"}
    assert proposal["core_api"] == "zddv.rerun.rerun_snapshot"
    assert proposal["historical_run"]["run_id"] == "run-fail"
    assert proposal["historical_run"]["recorded_inputs"] == {
        "test_name": "smoke",
        "seed": 7,
        "plusargs": ["+MODE=stress", "+COUNT=4"],
        "timeout_s": 25.0,
    }
    assert proposal["historical_run"]["replay_contract"] == {
        "backend": "current_configured_backend",
        "recorded_runtime_inputs_exact": True,
        "recorded_command_replayed_verbatim": False,
    }
    assert len(proposal["review_sha256"]) == 64


def test_execute_rerun_delegates_only_after_sha_approval(
    tmp_path: Path,
    monkeypatch,
):
    project, _ = _project(tmp_path)
    _record_historical_run(project)
    proposal = prepare_desktop_action(project, "rerun", run_id="run-fail")
    calls = []

    def fake_rerun(project_arg, snapshot):
        calls.append(
            (
                project_arg.root,
                snapshot["run_id"],
                snapshot["recorded_inputs"]["seed"],
            )
        )
        return {
            "status": "PASS",
            "selected": 1,
            "passed": 1,
            "failed": 0,
            "build": {"passed": True, "returncode": 0},
            "results": [],
            "source": snapshot,
        }

    monkeypatch.setattr("zddv.desktop_actions.rerun_snapshot", fake_rerun)

    with pytest.raises(RuntimeError, match="approval"):
        execute_desktop_action(
            project,
            proposal,
            expected_sha256=proposal["review_sha256"],
            approve_reviewed=False,
        )

    result = execute_desktop_action(
        project,
        proposal,
        expected_sha256=proposal["review_sha256"],
        approve_reviewed=True,
    )

    assert calls == [(project.root, "run-fail", 7)]
    assert result["action"] == "rerun"
    assert result["status"] == "PASS"
    assert result["review_sha256"] == proposal["review_sha256"]


def test_execute_rerun_rejects_historical_record_drift(
    tmp_path: Path,
    monkeypatch,
):
    project, _ = _project(tmp_path)
    _record_historical_run(project, seed=7)
    proposal = prepare_desktop_action(project, "rerun", run_id="run-fail")
    called = False

    def fake_rerun(_project_arg, _snapshot):
        nonlocal called
        called = True
        return {"status": "PASS"}

    monkeypatch.setattr("zddv.desktop_actions.rerun_snapshot", fake_rerun)
    _record_historical_run(project, seed=8)

    with pytest.raises(RuntimeError, match="changed after review"):
        execute_desktop_action(
            project,
            proposal,
            expected_sha256=proposal["review_sha256"],
            approve_reviewed=True,
        )

    assert called is False


def test_rerun_action_rejects_runtime_overrides(tmp_path: Path):
    project, _ = _project(tmp_path)
    _record_historical_run(project)

    with pytest.raises(ValueError, match="Runtime overrides"):
        prepare_desktop_action(
            project,
            "rerun",
            run_id="run-fail",
            seed=99,
        )


def test_rerun_action_requires_existing_run_id(tmp_path: Path):
    project, _ = _project(tmp_path)

    with pytest.raises(ValueError, match="requires a historical run_id"):
        prepare_desktop_action(project, "rerun")

    with pytest.raises(ValueError, match="Run not found"):
        prepare_desktop_action(project, "rerun", run_id="missing")

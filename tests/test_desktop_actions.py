from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import initialize_project
from zddv.desktop_actions import (
    execute_desktop_action,
    prepare_desktop_action,
)


def test_desktop_action_preview_is_deterministic_and_nonexecuting(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    first = prepare_desktop_action(
        project,
        "run",
        test_name="smoke",
        seed=42,
        plusargs=["+MODE=fast"],
        timeout_s=30.0,
    )
    second = prepare_desktop_action(
        project,
        "run",
        test_name="smoke",
        seed=42,
        plusargs=["+MODE=fast"],
        timeout_s=30.0,
    )

    assert first["request"] == second["request"]
    assert first["request_sha256"] == second["request_sha256"]
    assert first["request"]["project"]["root"] == str(project.root.resolve())
    assert first["execution_policy"] == {
        "requires_exact_sha256_confirmation": True,
        "arbitrary_shell_command": False,
        "automatic_execution": False,
    }


def test_desktop_action_rejects_wrong_confirmation_before_execution(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    prepared = prepare_desktop_action(project, "lint")
    calls = []

    def fail_if_called(project_arg):
        calls.append(project_arg.root)
        raise AssertionError("lint must not execute")

    monkeypatch.setattr("zddv.desktop_actions.lint_project", fail_if_called)

    with pytest.raises(RuntimeError, match="SHA-256 confirmation"):
        execute_desktop_action(
            project,
            prepared["request"],
            expected_sha256="0" * 64,
        )

    assert calls == []


def test_desktop_action_rejects_stale_project_identity(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    prepared = prepare_desktop_action(project, "build")
    project.top = "different_top"

    with pytest.raises(RuntimeError, match="active project identity"):
        execute_desktop_action(
            project,
            prepared["request"],
            expected_sha256=prepared["request_sha256"],
        )


def test_desktop_build_action_uses_structured_backend_call(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    prepared = prepare_desktop_action(project, "build")
    calls = []

    class BuildResult:
        returncode = 0
        log_path = project.root / ".zddv" / "build" / "build.log"
        executable = project.root / ".zddv" / "build" / "sim"
        artifact = None
        passed = True

    class Backend:
        def build(self, project_arg):
            calls.append(("build", project_arg.root))
            return BuildResult()

    monkeypatch.setattr(
        "zddv.desktop_actions.get_backend",
        lambda name: Backend(),
    )

    result = execute_desktop_action(
        project,
        prepared["request"],
        expected_sha256=prepared["request_sha256"],
    )

    assert calls == [("build", project.root)]
    assert result["status"] == "PASS"
    assert result["returncode"] == 0
    assert result["artifact"] == str(BuildResult.executable)


def test_desktop_run_action_passes_only_reviewed_structured_arguments(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    prepared = prepare_desktop_action(
        project,
        "run",
        test_name="stress",
        seed=7,
        plusargs=["+MODE=slow", "+COUNT=4"],
        timeout_s=15.0,
    )
    calls = []

    class Backend:
        def run(
            self,
            project_arg,
            *,
            test_name,
            seed,
            plusargs,
            timeout_s,
        ):
            calls.append(
                {
                    "root": project_arg.root,
                    "test_name": test_name,
                    "seed": seed,
                    "plusargs": plusargs,
                    "timeout_s": timeout_s,
                }
            )
            return SimpleNamespace(
                run_id="run-7",
                status="PASS",
                returncode=0,
                log_path=project.root / ".zddv" / "runs" / "run-7" / "simulation.log",
                waveform_path=None,
                coverage_path=None,
            )

    monkeypatch.setattr(
        "zddv.desktop_actions.get_backend",
        lambda name: Backend(),
    )

    result = execute_desktop_action(
        project,
        prepared["request"],
        expected_sha256=prepared["request_sha256"],
    )

    assert calls == [
        {
            "root": project.root,
            "test_name": "stress",
            "seed": 7,
            "plusargs": ["+MODE=slow", "+COUNT=4"],
            "timeout_s": 15.0,
        }
    ]
    assert result["action"] == "run"
    assert result["run_id"] == "run-7"
    assert result["status"] == "PASS"

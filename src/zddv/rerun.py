from __future__ import annotations

from typing import Any

from zddv.config import ProjectConfig
from zddv.simulator import SimulatorBackend, get_backend
from zddv.storage import get_run_record, list_run_records


DEFAULT_RERUN_STATUSES = ("FAIL", "TIMEOUT")


def select_historical_runs(
    project: ProjectConfig,
    *,
    limit: int = 20,
    statuses: tuple[str, ...] | list[str] = DEFAULT_RERUN_STATUSES,
) -> list[dict[str, Any]]:
    """Select persisted runs using the same status contract as the CLI."""
    return list_run_records(project, limit=limit, statuses=statuses)


def _historical_identity_errors(
    project: ProjectConfig,
    source: dict[str, Any],
) -> list[str]:
    expected = {
        "project": project.name,
        "simulator": project.simulator,
        "top": project.top,
    }
    return [
        f"{key}={source.get(key)!r} (expected {value!r})"
        for key, value in expected.items()
        if source.get(key) != value
    ]


def _snapshot_from_record(
    project: ProjectConfig,
    source: dict[str, Any],
) -> dict[str, Any]:
    identity_errors = _historical_identity_errors(project, source)
    if identity_errors:
        raise ValueError(
            "Historical run identity mismatch: " + "; ".join(identity_errors)
        )

    plusargs = source.get("plusargs")
    command = source.get("command")
    if not isinstance(plusargs, list) or any(
        not isinstance(item, str) for item in plusargs
    ):
        raise ValueError("Historical run plusargs must be a list of strings.")
    if not isinstance(command, list) or any(
        not isinstance(item, str) for item in command
    ):
        raise ValueError("Historical run command must be a list of strings.")

    return {
        "run_id": str(source["run_id"]),
        "created_at": source["created_at"],
        "status": source["status"],
        "returncode": source["returncode"],
        "duration_ms": source["duration_ms"],
        "identity": {
            "project": source["project"],
            "simulator": source["simulator"],
            "simulator_version": source["simulator_version"],
            "top": source["top"],
        },
        "recorded_inputs": {
            "test_name": source["test_name"],
            "seed": source["seed"],
            "plusargs": list(plusargs),
            "timeout_s": source["timeout_s"],
        },
        "recorded_command": list(command),
        "evidence": {
            "run_dir": source["run_dir"],
            "log_path": source["log_path"],
            "waveform_path": source["waveform_path"],
            "coverage_path": source["coverage_path"],
        },
        "replay_contract": {
            "backend": "current_configured_backend",
            "recorded_runtime_inputs_exact": True,
            "recorded_command_replayed_verbatim": False,
        },
    }


def historical_run_snapshot(
    project: ProjectConfig,
    run_id: str,
) -> dict[str, Any]:
    """Return the compatible persisted run evidence bound into reviewed replay."""
    source = get_run_record(project, run_id)
    if source is None:
        raise ValueError(f"Run not found: {run_id}")
    return _snapshot_from_record(project, source)


def _validated_snapshot_inputs(
    project: ProjectConfig,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("Historical run snapshot must be a JSON object.")

    identity = snapshot.get("identity")
    inputs = snapshot.get("recorded_inputs")
    if not isinstance(identity, dict) or not isinstance(inputs, dict):
        raise ValueError(
            "Historical run snapshot is missing identity or recorded inputs."
        )

    identity_errors = _historical_identity_errors(project, identity)
    if identity_errors:
        raise ValueError(
            "Historical run identity mismatch: " + "; ".join(identity_errors)
        )

    run_id = str(snapshot.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("Historical run snapshot is missing run_id.")

    test_name = inputs.get("test_name")
    if test_name is not None and not isinstance(test_name, str):
        raise ValueError("Historical run test_name must be a string or None.")

    seed = inputs.get("seed")
    if seed is not None and (
        not isinstance(seed, int) or isinstance(seed, bool)
    ):
        raise ValueError("Historical run seed must be an integer or None.")

    plusargs = inputs.get("plusargs")
    if not isinstance(plusargs, list) or any(
        not isinstance(item, str) for item in plusargs
    ):
        raise ValueError("Historical run plusargs must be a list of strings.")

    timeout_s = inputs.get("timeout_s")
    if timeout_s is not None and (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or timeout_s <= 0
    ):
        raise ValueError("Historical run timeout_s must be > 0 or None.")

    return {
        "run_id": run_id,
        "test_name": test_name,
        "seed": seed,
        "plusargs": list(plusargs),
        "timeout_s": timeout_s,
    }


def rerun_records(
    project: ProjectConfig,
    rows: list[dict[str, Any]],
    *,
    backend: SimulatorBackend | None = None,
) -> dict[str, Any]:
    """Build once, then rerun the exact recorded runtime inputs."""
    selected = [dict(row) for row in rows]
    if not selected:
        return {
            "status": "NO_RUNS",
            "selected": 0,
            "passed": 0,
            "failed": 0,
            "build": None,
            "results": [],
        }

    active_backend = backend or get_backend(project.simulator)
    build = active_backend.build(project)
    build_payload = {
        "passed": bool(build.passed),
        "returncode": int(build.returncode),
        "log_path": str(build.log_path),
        "command": list(build.command),
    }
    if not build.passed:
        return {
            "status": "BUILD_FAIL",
            "selected": len(selected),
            "passed": 0,
            "failed": len(selected),
            "build": build_payload,
            "results": [],
        }

    results: list[dict[str, Any]] = []
    passed = 0
    for source in reversed(selected):
        result = active_backend.run(
            project,
            test_name=source["test_name"],
            seed=source["seed"],
            plusargs=list(source["plusargs"]),
            timeout_s=source["timeout_s"],
        )
        if result.status == "PASS":
            passed += 1
        results.append(
            {
                "source_run_id": str(source["run_id"]),
                "run_id": str(result.run_id),
                "status": str(result.status),
                "returncode": int(result.returncode),
                "test_name": result.test_name,
                "seed": result.seed,
                "log_path": str(result.log_path),
                "waveform_path": (
                    None if result.waveform_path is None else str(result.waveform_path)
                ),
                "coverage_path": (
                    None if result.coverage_path is None else str(result.coverage_path)
                ),
            }
        )

    total = len(results)
    return {
        "status": "PASS" if passed == total else "FAIL",
        "selected": len(selected),
        "passed": passed,
        "failed": total - passed,
        "build": build_payload,
        "results": results,
    }


def rerun_snapshot(
    project: ProjectConfig,
    snapshot: dict[str, Any],
    *,
    backend: SimulatorBackend | None = None,
) -> dict[str, Any]:
    """Replay only the runtime inputs bound into a reviewed historical snapshot."""
    source = _validated_snapshot_inputs(project, snapshot)
    summary = rerun_records(project, [source], backend=backend)
    return {
        **summary,
        "source": snapshot,
    }


def rerun_run_id(
    project: ProjectConfig,
    run_id: str,
    *,
    backend: SimulatorBackend | None = None,
) -> dict[str, Any]:
    """Snapshot one compatible run, then replay its stored runtime inputs."""
    snapshot = historical_run_snapshot(project, run_id)
    return rerun_snapshot(project, snapshot, backend=backend)

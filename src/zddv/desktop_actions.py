from __future__ import annotations

import hashlib
import json
from typing import Any

from zddv.config import ProjectConfig
from zddv.lint import lint_project
from zddv.simulator import get_backend


_ALLOWED_ACTIONS = {"lint", "build", "run"}
_REQUEST_KEYS = {"schema_version", "project", "action", "parameters"}
_RUN_PARAMETER_KEYS = {"test_name", "seed", "plusargs", "timeout_s"}


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _request_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def prepare_desktop_action(
    project: ProjectConfig,
    action: str,
    *,
    test_name: str | None = None,
    seed: int | None = None,
    plusargs: list[str] | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Prepare one exact, reviewable GUI action without executing tools."""
    normalized_action = action.strip().lower()
    if normalized_action not in _ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported desktop action: {action}")

    if normalized_action != "run":
        if test_name is not None or seed is not None or plusargs or timeout_s is not None:
            raise ValueError(
                "test_name, seed, plusargs, and timeout_s are valid only for run"
            )
        parameters: dict[str, Any] = {}
    else:
        if seed is not None and seed < 0:
            raise ValueError("seed must be >= 0")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        parameters = {
            "test_name": test_name or None,
            "seed": seed,
            "plusargs": list(plusargs or []),
            "timeout_s": timeout_s,
        }

    if normalized_action == "lint" and project.simulator != "verilator":
        raise RuntimeError("Lint is currently implemented with Verilator only.")

    request = {
        "schema_version": 1,
        "project": {
            "name": project.name,
            "root": str(project.root.resolve()),
            "simulator": project.simulator,
            "top": project.top,
        },
        "action": normalized_action,
        "parameters": parameters,
    }
    digest = _request_sha256(request)
    return {
        "request": request,
        "request_sha256": digest,
        "review_text": json.dumps(request, indent=2, sort_keys=True),
        "execution_policy": {
            "requires_exact_sha256_confirmation": True,
            "arbitrary_shell_command": False,
            "automatic_execution": False,
        },
    }


def _verify_project_identity(
    project: ProjectConfig,
    request: dict[str, Any],
) -> None:
    expected = {
        "name": project.name,
        "root": str(project.root.resolve()),
        "simulator": project.simulator,
        "top": project.top,
    }
    if request.get("project") != expected:
        raise RuntimeError(
            "Reviewed desktop action does not match the active project identity."
        )


def _validate_reviewed_request(request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if set(request) != _REQUEST_KEYS or request.get("schema_version") != 1:
        raise ValueError("Reviewed desktop action request does not match schema v1.")

    action = request.get("action")
    parameters = request.get("parameters")
    if action not in _ALLOWED_ACTIONS or not isinstance(parameters, dict):
        raise ValueError("Reviewed desktop action request is invalid.")

    if action != "run":
        if parameters:
            raise ValueError(f"{action.title()} action must not contain parameters.")
        return str(action), parameters

    if set(parameters) != _RUN_PARAMETER_KEYS:
        raise ValueError("Run action parameters do not match the reviewed schema.")

    test_name = parameters["test_name"]
    seed = parameters["seed"]
    plusargs = parameters["plusargs"]
    timeout_s = parameters["timeout_s"]
    if test_name is not None and not isinstance(test_name, str):
        raise ValueError("Run test_name must be a string or null.")
    if seed is not None and (
        not isinstance(seed, int) or isinstance(seed, bool) or seed < 0
    ):
        raise ValueError("Run seed must be a non-negative integer or null.")
    if not isinstance(plusargs, list) or not all(
        isinstance(item, str) for item in plusargs
    ):
        raise ValueError("Run plusargs must be a list of strings.")
    if timeout_s is not None and (
        not isinstance(timeout_s, (int, float))
        or isinstance(timeout_s, bool)
        or timeout_s <= 0
    ):
        raise ValueError("Run timeout_s must be a positive number or null.")

    return str(action), parameters


def execute_desktop_action(
    project: ProjectConfig,
    request: dict[str, Any],
    *,
    expected_sha256: str,
) -> dict[str, Any]:
    """Execute only an exact reviewed structured action request."""
    actual_sha256 = _request_sha256(request)
    if expected_sha256.strip().lower() != actual_sha256:
        raise RuntimeError(
            "Desktop action SHA-256 confirmation does not match the reviewed request."
        )

    _verify_project_identity(project, request)
    action, parameters = _validate_reviewed_request(request)

    if action == "lint":
        if project.simulator != "verilator":
            raise RuntimeError("Lint is currently implemented with Verilator only.")
        result = lint_project(project)
        return {
            "action": "lint",
            "request_sha256": actual_sha256,
            "status": result["status"],
            "returncode": int(result["returncode"]),
            "log": result["log"],
            "summary": result["summary"],
            "details": {
                "errors": int(result["errors"]),
                "warnings": int(result["warnings"]),
            },
        }

    backend = get_backend(project.simulator)
    if action == "build":
        result = backend.build(project)
        artifact = result.executable or result.artifact
        return {
            "action": "build",
            "request_sha256": actual_sha256,
            "status": "PASS" if result.passed else "FAIL",
            "returncode": int(result.returncode),
            "log": str(result.log_path),
            "artifact": str(artifact) if artifact is not None else None,
        }

    result = backend.run(
        project,
        test_name=parameters["test_name"],
        seed=parameters["seed"],
        plusargs=parameters["plusargs"],
        timeout_s=parameters["timeout_s"],
    )
    return {
        "action": "run",
        "request_sha256": actual_sha256,
        "status": result.status,
        "returncode": int(result.returncode),
        "run_id": result.run_id,
        "log": str(result.log_path),
        "waveform": (
            str(result.waveform_path) if result.waveform_path is not None else None
        ),
        "coverage": (
            str(result.coverage_path) if result.coverage_path is not None else None
        ),
    }

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
from typing import Any

from zddv.config import ProjectConfig
from zddv.lint import lint_project
from zddv.rerun import historical_run_snapshot, rerun_snapshot
from zddv.simulator import get_backend


_ACTIONS = {"lint", "build", "run", "rerun"}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _project_path(project: ProjectConfig, path: Path) -> str:
    try:
        return path.resolve().relative_to(project.root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _source_manifest(project: ProjectConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in project.source_files():
        resolved = path.resolve()
        rows.append(
            {
                "path": _project_path(project, resolved),
                "bytes": resolved.stat().st_size,
                "sha256": _sha256_bytes(resolved.read_bytes()),
            }
        )
    rows.sort(key=lambda item: str(item["path"]))
    return rows


def _config_fingerprint(project: ProjectConfig) -> dict[str, Any]:
    path = project.config_path.resolve()
    if not path.is_file():
        return {
            "path": _project_path(project, path),
            "present": False,
            "sha256": None,
        }
    return {
        "path": _project_path(project, path),
        "present": True,
        "sha256": _sha256_bytes(path.read_bytes()),
    }


def prepare_desktop_action(
    project: ProjectConfig,
    action: str,
    *,
    run_id: str | None = None,
    test_name: str | None = None,
    seed: int | None = None,
    plusargs: list[str] | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Create a deterministic review payload without invoking a simulator."""
    normalized = action.strip().lower()
    if normalized not in _ACTIONS:
        raise ValueError(f"Unsupported desktop action: {action}")

    if normalized == "lint" and project.simulator != "verilator":
        raise RuntimeError("Desktop lint is currently implemented with Verilator only.")

    clean_run_id = "" if run_id is None else str(run_id).strip()
    clean_plusargs = [str(item) for item in (plusargs or [])]
    if timeout_s is not None and timeout_s <= 0:
        raise ValueError("timeout_s must be > 0")
    if seed is not None and not isinstance(seed, int):
        raise ValueError("seed must be an integer or None")

    has_run_parameters = (
        test_name is not None
        or seed is not None
        or bool(clean_plusargs)
        or timeout_s is not None
    )
    historical_run: dict[str, Any] | None = None

    parameters: dict[str, Any]
    if normalized == "run":
        if clean_run_id:
            raise ValueError("run_id is only valid for the rerun action.")
        parameters = {
            "test_name": test_name,
            "seed": seed,
            "plusargs": clean_plusargs,
            "timeout_s": timeout_s,
        }
    elif normalized == "rerun":
        if not clean_run_id:
            raise ValueError("rerun action requires a historical run_id.")
        if has_run_parameters:
            raise ValueError(
                "Runtime overrides are not valid for the rerun action; "
                "the persisted runtime inputs are used exactly."
            )
        parameters = {"run_id": clean_run_id}
        historical_run = historical_run_snapshot(project, clean_run_id)
    else:
        if clean_run_id:
            raise ValueError("run_id is only valid for the rerun action.")
        if has_run_parameters:
            raise ValueError("Run parameters are only valid for the run action.")
        parameters = {}

    effects = {
        "lint": [
            "Invokes Verilator lint.",
            "May create or replace .zddv/lint evidence artifacts.",
        ],
        "build": [
            f"Invokes the configured {project.simulator} build backend.",
            "May create or replace simulator build artifacts.",
        ],
        "run": [
            f"Invokes the configured {project.simulator} run backend.",
            "Creates a persisted run record and may create log, waveform, and coverage artifacts.",
        ],
        "rerun": [
            f"Builds the current project with the configured {project.simulator} backend.",
            "Uses the reviewed historical run's recorded test, seed, plusargs, and timeout exactly.",
            "The recorded historical command is review evidence and is not replayed verbatim.",
            "Creates a new persisted run record and may create log, waveform, and coverage artifacts.",
        ],
    }[normalized]

    core_api = {
        "lint": "zddv.lint.lint_project",
        "build": "zddv.simulator.get_backend(...).build",
        "run": "zddv.simulator.get_backend(...).run",
        "rerun": "zddv.rerun.rerun_snapshot",
    }[normalized]

    payload = {
        "schema_version": 1,
        "action": normalized,
        "project": {
            "name": project.name,
            "root": str(project.root.resolve()),
            "top": project.top,
            "simulator": project.simulator,
            "config": _config_fingerprint(project),
            "sources": _source_manifest(project),
        },
        "parameters": parameters,
        "core_api": core_api,
        "effects": effects,
        "review_policy": {
            "requires_exact_sha256": True,
            "requires_explicit_approval": True,
            "revalidates_project_before_execution": True,
        },
    }
    if historical_run is not None:
        payload["historical_run"] = historical_run
    return {**payload, "review_sha256": _canonical_sha256(payload)}


def _validated_current_proposal(
    project: ProjectConfig,
    proposal: dict[str, Any],
    *,
    expected_sha256: str,
    approve_reviewed: bool,
) -> dict[str, Any]:
    if not approve_reviewed:
        raise RuntimeError("Explicit reviewed-action approval is required.")

    reviewed_sha = str(proposal.get("review_sha256") or "")
    if not reviewed_sha:
        raise RuntimeError("Action proposal is missing review_sha256.")
    if expected_sha256 != reviewed_sha:
        raise RuntimeError("Expected SHA-256 does not match the reviewed action proposal.")

    canonical = {
        key: value
        for key, value in proposal.items()
        if key != "review_sha256"
    }
    if _canonical_sha256(canonical) != reviewed_sha:
        raise RuntimeError("Action proposal content does not match its review SHA-256.")

    action = str(proposal.get("action") or "")
    parameters = proposal.get("parameters")
    if not isinstance(parameters, dict):
        raise RuntimeError("Action proposal parameters are invalid.")

    current = prepare_desktop_action(
        project,
        action,
        run_id=parameters.get("run_id"),
        test_name=parameters.get("test_name"),
        seed=parameters.get("seed"),
        plusargs=parameters.get("plusargs"),
        timeout_s=parameters.get("timeout_s"),
    )
    if current["review_sha256"] != reviewed_sha:
        raise RuntimeError(
            "Project configuration, sources, or action parameters changed after review; "
            "prepare and review the action again."
        )
    return current


def execute_desktop_action(
    project: ProjectConfig,
    proposal: dict[str, Any],
    *,
    expected_sha256: str,
    approve_reviewed: bool,
) -> dict[str, Any]:
    """Execute one previously reviewed action through existing ZDDV core APIs."""
    reviewed = _validated_current_proposal(
        project,
        proposal,
        expected_sha256=expected_sha256.strip(),
        approve_reviewed=approve_reviewed,
    )
    action = reviewed["action"]
    parameters = reviewed["parameters"]

    if action == "lint":
        result = lint_project(project)
        return {
            "action": action,
            "review_sha256": reviewed["review_sha256"],
            "status": result["status"],
            "returncode": result["returncode"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "log": result["log"],
            "summary": result["summary"],
        }

    if action == "rerun":
        summary = rerun_snapshot(project, reviewed["historical_run"])
        return {
            "action": action,
            "review_sha256": reviewed["review_sha256"],
            **summary,
        }

    backend = get_backend(project.simulator)
    if action == "build":
        result = backend.build(project)
        return {
            "action": action,
            "review_sha256": reviewed["review_sha256"],
            "status": "PASS" if result.passed else "FAIL",
            "returncode": result.returncode,
            "log": str(result.log_path),
            "executable": str(result.executable) if result.executable else None,
            "artifact": str(result.artifact) if result.artifact else None,
        }

    result = backend.run(
        project,
        test_name=parameters["test_name"],
        seed=parameters["seed"],
        plusargs=parameters["plusargs"],
        timeout_s=parameters["timeout_s"],
    )
    return {
        "action": action,
        "review_sha256": reviewed["review_sha256"],
        "status": result.status,
        "returncode": result.returncode,
        "run_id": result.run_id,
        "test_name": result.test_name,
        "seed": result.seed,
        "log": str(result.log_path),
        "waveform": str(result.waveform_path) if result.waveform_path else None,
        "coverage": str(result.coverage_path) if result.coverage_path else None,
    }


def attach_desktop_actions_tab(
    notebook: Any,
    project: ProjectConfig,
) -> None:
    """Attach a SHA-confirmed project-action pane to the desktop notebook."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Desktop project actions require Python with tkinter support installed."
        ) from exc

    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Actions")

    state: dict[str, Any] = {"proposal": None}

    form = ttk.LabelFrame(tab, text="Prepare action for review", padding=8)
    form.pack(fill="x")

    action_var = tk.StringVar(value="lint")
    run_id_var = tk.StringVar(value="")
    test_var = tk.StringVar(value="")
    seed_var = tk.StringVar(value="")
    plusargs_var = tk.StringVar(value="")
    timeout_var = tk.StringVar(value="")

    ttk.Label(form, text="Action").grid(row=0, column=0, sticky="w")
    ttk.Combobox(
        form,
        textvariable=action_var,
        values=("lint", "build", "run", "rerun"),
        state="readonly",
        width=10,
    ).grid(row=0, column=1, sticky="w", padx=(4, 14))
    ttk.Label(form, text="Test").grid(row=0, column=2, sticky="w")
    ttk.Entry(form, textvariable=test_var, width=18).grid(
        row=0, column=3, sticky="w", padx=(4, 14)
    )
    ttk.Label(form, text="Seed").grid(row=0, column=4, sticky="w")
    ttk.Entry(form, textvariable=seed_var, width=10).grid(
        row=0, column=5, sticky="w", padx=(4, 14)
    )
    ttk.Label(form, text="Timeout").grid(row=0, column=6, sticky="w")
    ttk.Entry(form, textvariable=timeout_var, width=10).grid(
        row=0, column=7, sticky="w", padx=(4, 0)
    )

    ttk.Label(form, text="Plusargs").grid(row=1, column=0, sticky="w", pady=(8, 0))
    ttk.Entry(form, textvariable=plusargs_var, width=68).grid(
        row=1, column=1, columnspan=7, sticky="ew", padx=(4, 0), pady=(8, 0)
    )
    ttk.Label(form, text="Historical Run ID").grid(
        row=2, column=0, sticky="w", pady=(8, 0)
    )
    ttk.Entry(form, textvariable=run_id_var, width=68).grid(
        row=2, column=1, columnspan=7, sticky="ew", padx=(4, 0), pady=(8, 0)
    )
    form.columnconfigure(7, weight=1)

    review_status = tk.StringVar(
        value="Prepare an action. No simulator action runs during review preparation."
    )
    ttk.Label(tab, textvariable=review_status, anchor="w").pack(fill="x", pady=(8, 4))

    review_text = tk.Text(tab, height=15, wrap="none", state="disabled", font="TkFixedFont")
    review_text.pack(fill="both", expand=True)

    confirm = ttk.LabelFrame(tab, text="Execute reviewed action", padding=8)
    confirm.pack(fill="x", pady=(8, 0))
    confirm_sha = tk.StringVar(value="")
    approved = tk.BooleanVar(value=False)
    ttk.Label(confirm, text="Exact review SHA-256").pack(side="left")
    ttk.Entry(confirm, textvariable=confirm_sha, width=68).pack(
        side="left", padx=(6, 10), fill="x", expand=True
    )
    ttk.Checkbutton(
        confirm,
        text="I reviewed this exact action and approve execution",
        variable=approved,
    ).pack(side="left", padx=(0, 10))

    def _show(payload: dict[str, Any]) -> None:
        review_text.configure(state="normal")
        review_text.delete("1.0", "end")
        review_text.insert("1.0", json.dumps(payload, indent=2, sort_keys=True, default=str))
        review_text.configure(state="disabled")

    def _run_parameters() -> dict[str, Any]:
        seed_text = seed_var.get().strip()
        timeout_text = timeout_var.get().strip()
        return {
            "test_name": test_var.get().strip() or None,
            "seed": int(seed_text) if seed_text else None,
            "plusargs": shlex.split(plusargs_var.get()) if plusargs_var.get().strip() else [],
            "timeout_s": float(timeout_text) if timeout_text else None,
        }

    def prepare() -> None:
        try:
            action = action_var.get()
            if action == "run":
                params = _run_parameters()
            elif action == "rerun":
                params = {"run_id": run_id_var.get().strip() or None}
            else:
                params = {}
            proposal = prepare_desktop_action(project, action, **params)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            state["proposal"] = None
            review_status.set(f"Prepare error: {exc}")
            return

        state["proposal"] = proposal
        confirm_sha.set("")
        approved.set(False)
        _show(proposal)
        review_status.set(
            "Prepared only; nothing executed. Review SHA-256: "
            + proposal["review_sha256"]
        )

    def execute() -> None:
        proposal = state.get("proposal")
        if proposal is None:
            review_status.set("Prepare and review an action before execution.")
            return
        try:
            result = execute_desktop_action(
                project,
                proposal,
                expected_sha256=confirm_sha.get().strip(),
                approve_reviewed=bool(approved.get()),
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            review_status.set(f"Execution blocked/error: {exc}")
            return

        _show({"reviewed_action": proposal, "execution_result": result})
        review_status.set(
            f"{result['action'].upper()} {result['status']} · "
            f"review_sha256={result['review_sha256']}"
        )
        state["proposal"] = None
        confirm_sha.set("")
        approved.set(False)

    ttk.Button(form, text="Prepare for review", command=prepare).grid(
        row=2, column=7, sticky="e", pady=(8, 0)
    )
    ttk.Button(confirm, text="Execute", command=execute).pack(side="right")

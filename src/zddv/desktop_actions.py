from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from zddv.config import ProjectConfig
from zddv.simulator import SimulatorBackend, get_backend
from zddv.storage import get_run_record, list_run_records


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_run_record(
    project: ProjectConfig,
    run_id: str,
) -> dict[str, Any]:
    record = get_run_record(project, run_id)
    if record is None:
        raise RuntimeError(f"Historical run '{run_id}' was not found.")

    expected = {
        "project": project.name,
        "simulator": project.simulator,
        "top": project.top,
    }
    mismatches = [
        f"{key}={record.get(key)!r} (expected {value!r})"
        for key, value in expected.items()
        if record.get(key) != value
    ]
    if mismatches:
        raise RuntimeError(
            "Historical run identity mismatch: " + "; ".join(mismatches)
        )
    return record


def build_rerun_action_preview(
    project: ProjectConfig,
    run_id: str,
) -> dict[str, Any]:
    """Build a deterministic, non-executing review payload for one historical rerun."""
    record = _validated_run_record(project, run_id)
    payload = {
        "schema_version": 1,
        "action": "rerun",
        "project": {
            "name": project.name,
            "root": str(project.root.resolve()),
            "simulator": project.simulator,
            "top": project.top,
        },
        "source_run": {
            "run_id": record["run_id"],
            "status": record["status"],
            "test_name": record["test_name"],
            "seed": record["seed"],
            "plusargs": list(record["plusargs"]),
            "timeout_s": record["timeout_s"],
        },
        "effects": {
            "build_project": True,
            "execute_simulation": True,
            "write_run_artifacts": True,
            "update_results_database": True,
            "invoke_ai": False,
            "apply_generated_artifacts": False,
        },
    }
    confirmation_sha256 = _canonical_sha256(payload)
    return {
        **payload,
        "review": {
            "required": True,
            "confirmation_sha256": confirmation_sha256,
            "semantics": (
                "Execution is allowed only after explicit review of this exact payload. "
                "The confirmation fingerprint is recomputed immediately before execution."
            ),
        },
    }


def execute_rerun_action(
    project: ProjectConfig,
    run_id: str,
    *,
    confirmation_sha256: str,
    backend: SimulatorBackend | None = None,
) -> dict[str, Any]:
    """Execute one reviewed rerun only when the current preview fingerprint matches."""
    preview = build_rerun_action_preview(project, run_id)
    expected = str(preview["review"]["confirmation_sha256"])
    if not hmac.compare_digest(str(confirmation_sha256), expected):
        raise ValueError(
            "Rerun confirmation fingerprint does not match the current action preview."
        )

    selected_backend = backend or get_backend(project.simulator)
    build = selected_backend.build(project)
    build_result = {
        "passed": build.passed,
        "returncode": build.returncode,
        "log_path": str(build.log_path),
        "command": list(build.command),
    }
    if not build.passed:
        return {
            "schema_version": 1,
            "action": "rerun",
            "source_run_id": run_id,
            "confirmation_sha256": expected,
            "status": "BUILD_FAIL",
            "build": build_result,
            "run": None,
        }

    source = _validated_run_record(project, run_id)
    result = selected_backend.run(
        project,
        test_name=source["test_name"],
        seed=source["seed"],
        plusargs=list(source["plusargs"]),
        timeout_s=source["timeout_s"],
    )
    return {
        "schema_version": 1,
        "action": "rerun",
        "source_run_id": run_id,
        "confirmation_sha256": expected,
        "status": result.status,
        "build": build_result,
        "run": {
            "run_id": result.run_id,
            "status": result.status,
            "returncode": result.returncode,
            "test_name": result.test_name,
            "seed": result.seed,
            "run_dir": str(result.run_dir),
            "log_path": str(result.log_path),
            "waveform_path": (
                str(result.waveform_path) if result.waveform_path is not None else None
            ),
            "coverage_path": (
                str(result.coverage_path) if result.coverage_path is not None else None
            ),
        },
    }


def attach_desktop_actions_tab(
    notebook: Any,
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> None:
    """Attach explicit-review project actions to the desktop Debug Studio."""
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Desktop project actions require Python with tkinter support installed."
        ) from exc

    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Actions")

    info_var = tk.StringVar(
        value="Select a historical run, review its exact rerun payload, then confirm."
    )
    ttk.Label(tab, textvariable=info_var, anchor="w").pack(fill="x", pady=(0, 6))

    tree = ttk.Treeview(
        tab,
        columns=("status", "test", "seed", "run_id"),
        show="headings",
        height=10,
        selectmode="browse",
    )
    for column, title, width in (
        ("status", "Status", 90),
        ("test", "Test", 240),
        ("seed", "Seed", 90),
        ("run_id", "Run ID", 520),
    ):
        tree.heading(column, text=title)
        tree.column(column, width=width, anchor="w")
    tree.pack(fill="both", expand=True)

    review_text = tk.Text(tab, height=12, wrap="word", state="disabled")
    review_text.pack(fill="both", expand=True, pady=(8, 0))

    controls = ttk.Frame(tab, padding=(0, 8, 0, 0))
    controls.pack(fill="x")

    state: dict[str, Any] = {
        "run_id": None,
        "confirmation_sha256": None,
    }

    def clear_review() -> None:
        state["run_id"] = None
        state["confirmation_sha256"] = None
        confirm_button.state(["disabled"])
        review_text.configure(state="normal")
        review_text.delete("1.0", "end")
        review_text.configure(state="disabled")

    def refresh_runs() -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)
        clear_review()
        for row in list_run_records(project, limit=limit):
            tree.insert(
                "",
                "end",
                values=(
                    row["status"],
                    row["test_name"] or "(default)",
                    "-" if row["seed"] is None else row["seed"],
                    row["run_id"],
                ),
            )
        info_var.set("Run list refreshed. Select one run to review.")

    def selected_run_id() -> str | None:
        selection = tree.selection()
        if not selection:
            return None
        values = tree.item(selection[0], "values")
        return str(values[3])

    def review_selected() -> None:
        run_id = selected_run_id()
        if run_id is None:
            info_var.set("Select one historical run before review.")
            return
        try:
            preview = build_rerun_action_preview(project, run_id)
        except (RuntimeError, ValueError) as exc:
            clear_review()
            info_var.set(f"Review error: {exc}")
            return

        state["run_id"] = run_id
        state["confirmation_sha256"] = preview["review"]["confirmation_sha256"]
        confirm_button.state(["!disabled"])
        review_text.configure(state="normal")
        review_text.delete("1.0", "end")
        review_text.insert("1.0", json.dumps(preview, indent=2, sort_keys=True))
        review_text.configure(state="disabled")
        info_var.set(
            "Review ready. Confirm rerun only if the displayed inputs and effects are correct."
        )

    def confirm_rerun() -> None:
        run_id = state.get("run_id")
        token = state.get("confirmation_sha256")
        if not run_id or not token:
            info_var.set("Review a run before confirming execution.")
            return

        if not messagebox.askyesno(
            "Confirm ZDDV rerun",
            (
                f"Rerun historical run {run_id}?\n\n"
                "This will build the project, execute a simulation, write run artifacts, "
                "and update the results database."
            ),
            parent=tab.winfo_toplevel(),
        ):
            info_var.set("Rerun cancelled; no verification action was started.")
            return

        try:
            result = execute_rerun_action(
                project,
                run_id,
                confirmation_sha256=str(token),
            )
        except (RuntimeError, ValueError) as exc:
            info_var.set(f"Rerun blocked: {exc}")
            return

        review_text.configure(state="normal")
        review_text.delete("1.0", "end")
        review_text.insert("1.0", json.dumps(result, indent=2, sort_keys=True))
        review_text.configure(state="disabled")
        new_run = result.get("run") or {}
        if result["status"] == "BUILD_FAIL":
            info_var.set(
                f"Build failed. Review the build log: {result['build']['log_path']}"
            )
        else:
            info_var.set(
                f"Rerun finished: {result['status']} · new run {new_run.get('run_id')}"
            )
        state["confirmation_sha256"] = None
        confirm_button.state(["disabled"])

    tree.bind("<<TreeviewSelect>>", lambda _event: clear_review())

    ttk.Button(controls, text="Refresh runs", command=refresh_runs).pack(side="right")
    ttk.Button(controls, text="Review rerun", command=review_selected).pack(side="left")
    confirm_button = ttk.Button(
        controls,
        text="Confirm & rerun",
        command=confirm_rerun,
    )
    confirm_button.pack(side="left", padx=(8, 0))
    confirm_button.state(["disabled"])

    refresh_runs()

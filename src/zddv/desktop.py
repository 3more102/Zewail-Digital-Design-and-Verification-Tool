from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.design_index import build_design_index
from zddv.design_revision import design_revision_fingerprint
from zddv.desktop_actions import attach_desktop_actions_tab
from zddv.desktop_review import attach_desktop_review_tab
from zddv.desktop_waveform import attach_desktop_waveform_tab
from zddv.storage import (
    assertion_statistics,
    list_assertion_events,
    list_coverage_score_snapshots,
    list_coverage_snapshots,
    list_formal_property_results,
    list_formal_result_snapshots,
    list_run_records,
    list_uvm_log_snapshots,
    list_uvm_report_messages,
    run_statistics,
)
from zddv.triage import group_failure_records


def _latest_coverage(project: ProjectConfig) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []

    score_rows = list_coverage_score_snapshots(project, limit=1)
    if score_rows:
        row = score_rows[0]
        candidates.append(
            {
                "snapshot_id": row["snapshot_id"],
                "created_at": row["created_at"],
                "simulator": row["simulator"],
                "kind": "score",
                "percent": float(row["score"]),
                "breakdown": row["by_metric"],
                "explicit_counts": row["by_metric_counts"],
            }
        )

    point_rows = list_coverage_snapshots(project, limit=1)
    if point_rows:
        row = point_rows[0]
        candidates.append(
            {
                "snapshot_id": row["snapshot_id"],
                "created_at": row["created_at"],
                "simulator": row["simulator"],
                "kind": "points",
                "percent": float(row["hit_rate"]),
                "hit_points": int(row["hit_points"]),
                "total_points": int(row["total_points"]),
                "breakdown": row["by_type"],
                "explicit_counts": {},
            }
        )

    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item["created_at"]))


def _load_persisted_elaborated_hierarchy(project: ProjectConfig) -> dict[str, Any]:
    path = project.root / ".zddv" / "design" / "elaborated.json"
    if not path.exists():
        return {
            "status": "NOT_PRESENT",
            "path": str(path),
            "instances": [],
            "ports": [],
            "port_evidence": {"status": "NOT_PRESENT"},
            "pin_bindings": [],
            "pin_binding_evidence": {"status": "NOT_PRESENT"},
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "INVALID",
            "path": str(path),
            "error": str(exc),
            "instances": [],
            "ports": [],
            "port_evidence": {"status": "INVALID"},
            "pin_bindings": [],
            "pin_binding_evidence": {"status": "INVALID"},
        }

    instances = payload.get("instances") if isinstance(payload, dict) else None
    if not isinstance(instances, list):
        return {
            "status": "INVALID",
            "path": str(path),
            "error": "elaborated hierarchy payload must contain an instances list",
            "instances": [],
            "ports": [],
            "port_evidence": {"status": "INVALID"},
            "pin_bindings": [],
            "pin_binding_evidence": {"status": "INVALID"},
        }

    ports = payload.get("ports", [])
    if not isinstance(ports, list):
        return {
            "status": "INVALID",
            "path": str(path),
            "error": "elaborated hierarchy payload ports must be a list",
            "instances": [],
            "ports": [],
            "port_evidence": {"status": "INVALID"},
            "pin_bindings": [],
            "pin_binding_evidence": {"status": "INVALID"},
        }

    port_evidence = payload.get("port_evidence")
    if port_evidence is None:
        port_evidence = {
            "status": "UNAVAILABLE",
            "reason": "port_evidence_metadata_missing",
        }
    elif not isinstance(port_evidence, dict):
        return {
            "status": "INVALID",
            "path": str(path),
            "error": "elaborated hierarchy port_evidence must be an object",
            "instances": [],
            "ports": [],
            "port_evidence": {"status": "INVALID"},
            "pin_bindings": [],
            "pin_binding_evidence": {"status": "INVALID"},
        }
    elif port_evidence.get("status") == "NORMALIZED" and "ports" not in payload:
        return {
            "status": "INVALID",
            "path": str(path),
            "error": "normalized port_evidence requires a ports list",
            "instances": [],
            "ports": [],
            "port_evidence": {"status": "INVALID"},
            "pin_bindings": [],
            "pin_binding_evidence": {"status": "INVALID"},
        }

    # Fail closed: persisted port rows are displayable only when the evidence
    # contract explicitly says they were normalized from the supported schema.
    if port_evidence.get("status") != "NORMALIZED":
        ports = []

    pin_bindings = payload.get("pin_bindings", [])
    if not isinstance(pin_bindings, list):
        return {
            "status": "INVALID",
            "path": str(path),
            "error": "elaborated hierarchy payload pin_bindings must be a list",
            "instances": [],
            "ports": [],
            "port_evidence": {"status": "INVALID"},
            "pin_bindings": [],
            "pin_binding_evidence": {"status": "INVALID"},
        }

    pin_binding_evidence = payload.get("pin_binding_evidence")
    if pin_binding_evidence is None:
        pin_binding_evidence = {
            "status": "UNAVAILABLE",
            "reason": "pin_binding_evidence_metadata_missing",
        }
    elif not isinstance(pin_binding_evidence, dict):
        return {
            "status": "INVALID",
            "path": str(path),
            "error": "elaborated hierarchy pin_binding_evidence must be an object",
            "instances": [],
            "ports": [],
            "port_evidence": {"status": "INVALID"},
            "pin_bindings": [],
            "pin_binding_evidence": {"status": "INVALID"},
        }
    elif (
        pin_binding_evidence.get("status") == "NORMALIZED"
        and "pin_bindings" not in payload
    ):
        return {
            "status": "INVALID",
            "path": str(path),
            "error": "normalized pin_binding_evidence requires a pin_bindings list",
            "instances": [],
            "ports": [],
            "port_evidence": {"status": "INVALID"},
            "pin_bindings": [],
            "pin_binding_evidence": {"status": "INVALID"},
        }

    # Direct pin-binding rows can contain both NORMALIZED direct VARREF bindings
    # and explicitly UNSUPPORTED complex expressions. Expose either only when the
    # persisted evidence contract itself is normalized.
    if pin_binding_evidence.get("status") != "NORMALIZED":
        pin_bindings = []

    identity_errors: list[str] = []
    for field, expected in (
        ("project", project.name),
        ("top", project.top),
        ("simulator", project.simulator),
    ):
        actual = payload.get(field)
        if actual != expected:
            identity_errors.append(
                f"{field} mismatch: expected {expected!r}, found {actual!r}"
            )

    current_fingerprint = design_revision_fingerprint(project)
    stored_fingerprint = payload.get("design_fingerprint")
    if stored_fingerprint != current_fingerprint:
        if stored_fingerprint is None:
            identity_errors.append("design_fingerprint is missing")
        else:
            identity_errors.append(
                "design_fingerprint does not match the current RTL/config revision"
            )

    if identity_errors:
        return {
            "status": "STALE",
            "path": str(path),
            "error": "; ".join(identity_errors),
            "created_at": payload.get("created_at"),
            "simulator": payload.get("simulator"),
            "simulator_version": payload.get("simulator_version"),
            "source_format": payload.get("source_format"),
            "design_fingerprint": stored_fingerprint,
            "current_design_fingerprint": current_fingerprint,
            "instances": [],
            "ports": [],
            "port_evidence": {
                "status": "STALE",
                "reason": "elaborated_evidence_identity_mismatch",
            },
            "pin_bindings": [],
            "pin_binding_evidence": {
                "status": "STALE",
                "reason": "elaborated_evidence_identity_mismatch",
            },
        }

    return {
        "status": "PRESENT",
        "path": str(path),
        "created_at": payload.get("created_at"),
        "simulator": payload.get("simulator"),
        "simulator_version": payload.get("simulator_version"),
        "source_format": payload.get("source_format"),
        "summary": payload.get("summary") or {},
        "design_fingerprint": stored_fingerprint,
        "current_design_fingerprint": current_fingerprint,
        "instances": instances,
        "ports": ports,
        "port_evidence": port_evidence,
        "pin_bindings": pin_bindings,
        "pin_binding_evidence": pin_binding_evidence,
    }


def _read_source(
    project: ProjectConfig,
    value: str,
    *,
    allowed_paths: set[Path] | None = None,
) -> str:
    """Read one approved source file for display without mutating project state."""
    root = project.root.resolve()
    path = Path(value)
    candidate = (path if path.is_absolute() else root / path).resolve()

    if allowed_paths is None:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("source path must remain inside the project root") from exc
    elif candidate not in allowed_paths:
        raise ValueError("source path is not part of the current design index")

    return candidate.read_text(encoding="utf-8", errors="replace")


def build_desktop_snapshot(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Compose display-only Debug Studio state from persisted ZDDV evidence."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    runs = list_run_records(project, limit=limit)
    formal_rows = list_formal_result_snapshots(project, limit=1)
    uvm_rows = list_uvm_log_snapshots(project, limit=1)
    latest_formal = formal_rows[0] if formal_rows else None
    latest_uvm = uvm_rows[0] if uvm_rows else None
    formal_properties = (
        list_formal_property_results(
            project,
            str(latest_formal["snapshot_id"]),
            limit=limit,
        )
        if latest_formal is not None
        else []
    )
    uvm_messages = (
        list_uvm_report_messages(
            project,
            str(latest_uvm["snapshot_id"]),
            limit=limit,
        )
        if latest_uvm is not None
        else []
    )
    design = build_design_index(project)

    return {
        "project": {
            "name": project.name,
            "root": str(project.root),
            "top": project.top,
            "simulator": project.simulator,
        },
        "policy": {
            "scope": "snapshot_refresh",
            "display_only": True,
            "executes_verification": False,
            "invokes_ai": False,
            "applies_generated_artifacts": False,
            "review_gated_execution_available": True,
            "review_gated_generated_apply_available": True,
        },
        "stats": run_statistics(project),
        "assertions": assertion_statistics(project),
        "assertion_events": list_assertion_events(project, limit=limit),
        "recent_runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": latest_formal,
        "formal_properties": formal_properties,
        "latest_uvm": latest_uvm,
        "uvm_messages": uvm_messages,
        "design": design,
        "elaborated_hierarchy": _load_persisted_elaborated_hierarchy(project),
    }


def launch_desktop_gui(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Launch Debug Studio evidence views plus explicit review-gated actions."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise RuntimeError(
            "Desktop GUI requires Python with tkinter support installed."
        ) from exc

    try:
        window = tk.Tk()
    except Exception as exc:  # pragma: no cover - display server/platform dependent
        raise RuntimeError(f"Desktop GUI could not start: {exc}") from exc

    window.title(f"ZDDV Debug Studio — {project.name}")
    window.geometry("1180x760")
    window.minsize(900, 600)

    container = ttk.Frame(window, padding=12)
    container.pack(fill="both", expand=True)

    header = ttk.Frame(container)
    header.pack(fill="x")
    ttk.Label(
        header,
        text="ZDDV Debug Studio",
        font=("TkDefaultFont", 16, "bold"),
    ).pack(side="left")

    status_text = tk.StringVar(value="")
    ttk.Label(header, textvariable=status_text).pack(side="right")

    summary = ttk.Frame(container, padding=(0, 12, 0, 8))
    summary.pack(fill="x")
    summary_vars = {
        name: tk.StringVar(value="-")
        for name in ("Runs", "Passed", "Failed", "Timeouts", "Pass rate", "Coverage")
    }
    for column, (name, variable) in enumerate(summary_vars.items()):
        frame = ttk.LabelFrame(summary, text=name, padding=8)
        frame.grid(row=0, column=column, padx=(0, 8), sticky="nsew")
        ttk.Label(frame, textvariable=variable).pack()
        summary.columnconfigure(column, weight=1)

    notebook = ttk.Notebook(container)
    notebook.pack(fill="both", expand=True)

    run_tab = ttk.Frame(notebook, padding=8)
    failure_tab = ttk.Frame(notebook, padding=8)
    evidence_tab = ttk.Frame(notebook, padding=8)
    sources_tab = ttk.Frame(notebook, padding=8)
    hierarchy_tab = ttk.Frame(notebook, padding=8)
    elaborated_tab = ttk.Frame(notebook, padding=8)
    notebook.add(run_tab, text="Recent Runs")
    notebook.add(failure_tab, text="Failure Groups")
    notebook.add(evidence_tab, text="Evidence")
    notebook.add(sources_tab, text="Sources")
    notebook.add(hierarchy_tab, text="Hierarchy")
    assertions_tab = ttk.Frame(notebook, padding=8)
    formal_tab = ttk.Frame(notebook, padding=8)
    uvm_tab = ttk.Frame(notebook, padding=8)
    notebook.add(assertions_tab, text="Assertions")
    notebook.add(formal_tab, text="Formal")
    notebook.add(uvm_tab, text="UVM")
    notebook.add(elaborated_tab, text="Elaborated")
    attach_desktop_waveform_tab(notebook, project)
    attach_desktop_actions_tab(notebook, project)
    attach_desktop_review_tab(notebook, project, limit=limit)

    run_columns = ("status", "test", "seed", "duration", "run_id")
    run_tree = ttk.Treeview(run_tab, columns=run_columns, show="headings")
    for column, title, width in (
        ("status", "Status", 90),
        ("test", "Test", 220),
        ("seed", "Seed", 90),
        ("duration", "Time (ms)", 110),
        ("run_id", "Run ID", 420),
    ):
        run_tree.heading(column, text=title)
        run_tree.column(column, width=width, anchor="w")
    run_tree.pack(fill="both", expand=True)

    failure_columns = ("count", "status", "tests", "signature")
    failure_tree = ttk.Treeview(
        failure_tab,
        columns=failure_columns,
        show="headings",
    )
    for column, title, width in (
        ("count", "Count", 80),
        ("status", "Status", 120),
        ("tests", "Tests", 260),
        ("signature", "Normalized signature", 520),
    ):
        failure_tree.heading(column, text=title)
        failure_tree.column(column, width=width, anchor="w")
    failure_tree.pack(fill="both", expand=True)

    evidence_columns = ("kind", "status", "details", "id")
    evidence_tree = ttk.Treeview(
        evidence_tab,
        columns=evidence_columns,
        show="headings",
    )
    for column, title, width in (
        ("kind", "Evidence", 150),
        ("status", "Status", 130),
        ("details", "Details", 500),
        ("id", "Snapshot / source", 300),
    ):
        evidence_tree.heading(column, text=title)
        evidence_tree.column(column, width=width, anchor="w")
    evidence_tree.pack(fill="both", expand=True)

    source_columns = ("kind", "location", "details")
    source_pane = ttk.Panedwindow(sources_tab, orient="horizontal")
    source_pane.pack(fill="both", expand=True)
    source_left = ttk.Frame(source_pane)
    source_right = ttk.Frame(source_pane)
    source_pane.add(source_left, weight=2)
    source_pane.add(source_right, weight=5)

    source_tree = ttk.Treeview(
        source_left,
        columns=source_columns,
        show="tree headings",
    )
    source_tree.heading("#0", text="Source / unit")
    source_tree.column("#0", width=300, anchor="w")
    for column, title, width in (
        ("kind", "Kind", 90),
        ("location", "Location", 220),
        ("details", "Evidence", 360),
    ):
        source_tree.heading(column, text=title)
        source_tree.column(column, width=width, anchor="w")
    source_tree.pack(fill="both", expand=True)

    source_title_text = tk.StringVar(value="Select a source file or design unit.")
    ttk.Label(source_right, textvariable=source_title_text).grid(
        row=0,
        column=0,
        columnspan=2,
        sticky="ew",
        pady=(0, 6),
    )
    source_text = tk.Text(source_right, wrap="none")
    source_ybar = ttk.Scrollbar(
        source_right,
        orient="vertical",
        command=source_text.yview,
    )
    source_xbar = ttk.Scrollbar(
        source_right,
        orient="horizontal",
        command=source_text.xview,
    )
    source_text.configure(
        yscrollcommand=source_ybar.set,
        xscrollcommand=source_xbar.set,
    )
    source_text.grid(row=1, column=0, sticky="nsew")
    source_ybar.grid(row=1, column=1, sticky="ns")
    source_xbar.grid(row=2, column=0, sticky="ew")
    source_right.rowconfigure(1, weight=1)
    source_right.columnconfigure(0, weight=1)
    source_text.configure(state="disabled")

    hierarchy_columns = ("type", "source", "state")
    hierarchy_tree = ttk.Treeview(
        hierarchy_tab,
        columns=hierarchy_columns,
        show="tree headings",
    )
    hierarchy_tree.heading("#0", text="Instance")
    hierarchy_tree.column("#0", width=320, anchor="w")
    for column, title, width in (
        ("type", "Type", 220),
        ("source", "Source", 320),
        ("state", "State", 120),
    ):
        hierarchy_tree.heading(column, text=title)
        hierarchy_tree.column(column, width=width, anchor="w")
    hierarchy_tree.pack(fill="both", expand=True)


    elaborated_columns = ("module", "direction", "source", "state")
    elaborated_tree = ttk.Treeview(
        elaborated_tab,
        columns=elaborated_columns,
        show="tree headings",
    )
    elaborated_tree.heading("#0", text="Hierarchy path")
    elaborated_tree.column("#0", width=420, anchor="w")
    for column, title, width in (
        ("module", "Module", 200),
        ("direction", "Direction", 100),
        ("source", "Source", 300),
        ("state", "State", 120),
    ):
        elaborated_tree.heading(column, text=title)
        elaborated_tree.column(column, width=width, anchor="w")
    elaborated_tree.pack(fill="both", expand=True)

    assertion_columns = ("status", "name", "run", "line", "message")
    assertion_tree = ttk.Treeview(assertions_tab, columns=assertion_columns, show="headings")
    for column, title, width in (
        ("status", "Status", 90),
        ("name", "Assertion", 240),
        ("run", "Run ID", 260),
        ("line", "Log line", 90),
        ("message", "Message", 480),
    ):
        assertion_tree.heading(column, text=title)
        assertion_tree.column(column, width=width, anchor="w")
    assertion_tree.pack(fill="both", expand=True)

    formal_columns = ("status", "kind", "name", "interpretation", "depth", "message")
    formal_tree = ttk.Treeview(formal_tab, columns=formal_columns, show="headings")
    for column, title, width in (
        ("status", "Status", 90),
        ("kind", "Kind", 90),
        ("name", "Property", 240),
        ("interpretation", "Interpretation", 170),
        ("depth", "Depth", 90),
        ("message", "Message", 390),
    ):
        formal_tree.heading(column, text=title)
        formal_tree.column(column, width=width, anchor="w")
    formal_tree.pack(fill="both", expand=True)

    uvm_columns = ("severity", "report_id", "component", "time", "line", "message")
    uvm_tree = ttk.Treeview(uvm_tab, columns=uvm_columns, show="headings")
    for column, title, width in (
        ("severity", "Severity", 120),
        ("report_id", "Report ID", 140),
        ("component", "Component", 230),
        ("time", "Time", 100),
        ("line", "Log line", 90),
        ("message", "Message", 400),
    ):
        uvm_tree.heading(column, text=title)
        uvm_tree.column(column, width=width, anchor="w")
    uvm_tree.pack(fill="both", expand=True)

    footer = ttk.Frame(container, padding=(0, 10, 0, 0))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        text=(
            "Evidence refresh/source preview are read-only. Actions and generated-artifact "
            "apply require explicit SHA-confirmed review; the GUI never auto-invokes AI "
            "or silently executes generated code."
        ),
    ).pack(side="left")

    current: dict[str, Any] = {}
    source_targets: dict[str, tuple[str, int | None]] = {}
    hierarchy_targets: dict[str, tuple[str, int | None]] = {}
    elaborated_targets: dict[str, tuple[str, int | None]] = {}
    allowed_source_paths: set[Path] = set()

    def _clear(tree) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _show_source(path_value: str, line: int | None = None) -> None:
        try:
            text = _read_source(
                project,
                path_value,
                allowed_paths=allowed_source_paths,
            )
        except (OSError, ValueError) as exc:
            source_title_text.set(f"{path_value} · unavailable: {exc}")
            text = ""
            line = None

        source_text.configure(state="normal")
        source_text.delete("1.0", "end")
        source_text.insert("1.0", text)
        source_text.configure(state="disabled")
        if line is not None and line > 0:
            source_text.see(f"{line}.0")
            source_title_text.set(f"{path_value}:{line}")
        else:
            source_title_text.set(path_value)

    def _on_source_select(_event=None) -> None:
        selection = source_tree.selection()
        if not selection:
            return
        target = source_targets.get(selection[0])
        if target is not None:
            _show_source(*target)

    def _open_hierarchy_source(tree, targets) -> None:
        selection = tree.selection()
        if not selection:
            return
        target = targets.get(selection[0])
        if target is None:
            return
        notebook.select(sources_tab)
        _show_source(*target)

    source_tree.bind("<<TreeviewSelect>>", _on_source_select)
    hierarchy_tree.bind(
        "<Double-1>",
        lambda _event: _open_hierarchy_source(hierarchy_tree, hierarchy_targets),
    )
    elaborated_tree.bind(
        "<Double-1>",
        lambda _event: _open_hierarchy_source(elaborated_tree, elaborated_targets),
    )

    def refresh() -> None:
        nonlocal current
        current = build_desktop_snapshot(project, limit=limit)
        stats = current["stats"]
        coverage = current["latest_coverage"]

        summary_vars["Runs"].set(str(stats["total"]))
        summary_vars["Passed"].set(str(stats["passed"]))
        summary_vars["Failed"].set(str(stats["failed"]))
        summary_vars["Timeouts"].set(str(stats["timed_out"]))
        summary_vars["Pass rate"].set(f"{stats['pass_rate']:.1f}%")
        summary_vars["Coverage"].set(
            "-" if coverage is None else f"{coverage['percent']:.1f}%"
        )

        _clear(run_tree)
        for row in current["recent_runs"]:
            duration = "-" if row["duration_ms"] is None else f"{row['duration_ms']:.1f}"
            run_tree.insert(
                "",
                "end",
                values=(
                    row["status"],
                    row["test_name"] or "(default)",
                    "-" if row["seed"] is None else row["seed"],
                    duration,
                    row["run_id"],
                ),
            )

        _clear(failure_tree)
        for group in current["failure_groups"]:
            failure_tree.insert(
                "",
                "end",
                values=(
                    group["count"],
                    ", ".join(group["statuses"]),
                    ", ".join(group["tests"]) or "-",
                    group["signature"],
                ),
            )


        design = current["design"]
        allowed_source_paths.clear()
        for source in design["files"]:
            source_path = Path(source["path"])
            if not source_path.is_absolute():
                source_path = project.root / source_path
            allowed_source_paths.add(source_path.resolve())

        units_by_file: dict[str, list[dict[str, Any]]] = {}
        for unit in design["units"]:
            units_by_file.setdefault(unit["file"], []).append(unit)

        source_targets.clear()
        _clear(source_tree)
        for source in design["files"]:
            file_id = source_tree.insert(
                "",
                "end",
                text=source["path"],
                values=(
                    "file",
                    "",
                    f"{source['lines']} lines · {source['bytes']} bytes · "
                    f"sha256={source['sha256']}",
                ),
                open=True,
            )
            source_targets[file_id] = (source["path"], 1)
            for unit in units_by_file.get(source["path"], []):
                unit_id = source_tree.insert(
                    file_id,
                    "end",
                    text=unit["name"],
                    values=(
                        unit["kind"],
                        f"{unit['file']}:{unit['line']}",
                        f"lines {unit['line']}-{unit['end_line']} · "
                        f"{len(unit['instances'])} child instances",
                    ),
                )
                source_targets[unit_id] = (unit["file"], int(unit["line"]))

        hierarchy_targets.clear()
        _clear(hierarchy_tree)

        def _insert_hierarchy(parent: str, node: dict[str, Any]) -> None:
            if not node.get("resolved", True):
                state = "UNRESOLVED"
            elif node.get("recursive"):
                state = "RECURSIVE"
            else:
                state = "RESOLVED"

            source = "-"
            if node.get("file") is not None and node.get("line") is not None:
                source = f"{node['file']}:{node['line']}"

            item_id = hierarchy_tree.insert(
                parent,
                "end",
                text=node["instance"],
                values=(node["type"], source, state),
                open=True,
            )
            if node.get("file") is not None:
                hierarchy_targets[item_id] = (
                    str(node["file"]),
                    int(node["line"]) if node.get("line") else None,
                )
            for child in node.get("children", []):
                _insert_hierarchy(item_id, child)

        _insert_hierarchy("", design["hierarchy"])

        elaborated_targets.clear()
        _clear(elaborated_tree)
        elaborated = current["elaborated_hierarchy"]
        if elaborated["status"] == "PRESENT":
            ports_by_module: dict[str, list[dict[str, Any]]] = {}
            port_directions: dict[tuple[str, str], str] = {}
            for port in elaborated.get("ports", []):
                module_name = str(port.get("module") or "")
                port_name = str(port.get("name") or "")
                if module_name:
                    ports_by_module.setdefault(module_name, []).append(port)
                if module_name and port_name and port.get("direction"):
                    port_directions[(module_name, port_name)] = str(port["direction"])

            pin_bindings_by_instance: dict[str, list[dict[str, Any]]] = {}
            for binding in elaborated.get("pin_bindings", []):
                instance_path = str(binding.get("instance_path") or "")
                if instance_path:
                    pin_bindings_by_instance.setdefault(instance_path, []).append(binding)

            for row in elaborated["instances"]:
                location = row.get("location") or {}
                source = "-"
                if location.get("path"):
                    source = str(location["path"])
                    if location.get("line"):
                        source += f":{location['line']}"
                state = "TOP" if row.get("top") else "INSTANCE"
                item_id = elaborated_tree.insert(
                    "",
                    "end",
                    text=row.get("path") or row.get("name") or "-",
                    values=(row.get("module") or "-", "-", source, state),
                    open=True,
                )
                if location.get("path"):
                    elaborated_targets[item_id] = (
                        str(location["path"]),
                        int(location["line"]) if location.get("line") else None,
                    )

                module_name = str(row.get("module") or "")
                for port in ports_by_module.get(module_name, []):
                    port_location = port.get("location") or {}
                    port_source = "-"
                    if port_location.get("path"):
                        port_source = str(port_location["path"])
                        if port_location.get("line"):
                            port_source += f":{port_location['line']}"
                    port_item_id = elaborated_tree.insert(
                        item_id,
                        "end",
                        text=port.get("name") or "-",
                        values=(
                            port.get("module") or "-",
                            port.get("direction") or "-",
                            port_source,
                            "PORT",
                        ),
                    )
                    if port_location.get("path"):
                        elaborated_targets[port_item_id] = (
                            str(port_location["path"]),
                            int(port_location["line"])
                            if port_location.get("line")
                            else None,
                        )

                instance_path = str(row.get("path") or "")
                for binding in pin_bindings_by_instance.get(instance_path, []):
                    pin = str(binding.get("pin") or "-")
                    parent_path = str(binding.get("parent_instance_path") or "-")
                    parent_signal = binding.get("signal")
                    expression_type = str(binding.get("expression_type") or "")
                    target = (
                        f"{parent_path}.{parent_signal}"
                        if parent_signal
                        else f"{parent_path}.<{expression_type or 'expression'}>"
                    )
                    binding_location = (
                        binding.get("pin_location")
                        or binding.get("signal_location")
                        or {}
                    )
                    binding_source = "-"
                    if binding_location.get("path"):
                        binding_source = str(binding_location["path"])
                        if binding_location.get("line"):
                            binding_source += f":{binding_location['line']}"

                    binding_item_id = elaborated_tree.insert(
                        item_id,
                        "end",
                        text=f"{pin} -> {target}",
                        values=(
                            binding.get("instance_module") or row.get("module") or "-",
                            port_directions.get(
                                (str(row.get("module") or ""), pin),
                                "-",
                            ),
                            binding_source,
                            binding.get("status") or "UNKNOWN",
                        ),
                    )
                    if binding_location.get("path"):
                        elaborated_targets[binding_item_id] = (
                            str(binding_location["path"]),
                            int(binding_location["line"])
                            if binding_location.get("line")
                            else None,
                        )

            port_evidence = elaborated.get("port_evidence") or {}
            if port_evidence.get("status") != "NORMALIZED":
                elaborated_tree.insert(
                    "",
                    "end",
                    text="Module-port evidence",
                    values=(
                        "-",
                        "-",
                        port_evidence.get("reason")
                        or port_evidence.get("source_format")
                        or "-",
                        port_evidence.get("status") or "UNAVAILABLE",
                    ),
                )

            pin_binding_evidence = elaborated.get("pin_binding_evidence") or {}
            if pin_binding_evidence.get("status") != "NORMALIZED":
                elaborated_tree.insert(
                    "",
                    "end",
                    text="Pin-binding evidence",
                    values=(
                        "-",
                        "-",
                        pin_binding_evidence.get("reason")
                        or pin_binding_evidence.get("source_format")
                        or "-",
                        pin_binding_evidence.get("status") or "UNAVAILABLE",
                    ),
                )
        else:
            elaborated_tree.insert(
                "",
                "end",
                text=elaborated["status"],
                values=(
                    "-",
                    "-",
                    elaborated.get("error") or elaborated["path"],
                    elaborated["status"],
                ),
            )

        _clear(assertion_tree)
        for event in current["assertion_events"]:
            assertion_tree.insert(
                "",
                "end",
                values=(
                    event["status"],
                    event["assertion_name"],
                    event["run_id"],
                    "-" if event["log_line"] is None else event["log_line"],
                    event["message"] or "",
                ),
            )

        _clear(formal_tree)
        for item in current["formal_properties"]:
            depth = (
                item["effective_depth"]
                if item["effective_depth"] is not None
                else item["depth"]
            )
            formal_tree.insert(
                "",
                "end",
                values=(
                    item["status"],
                    item["kind"],
                    item["name"],
                    item["interpretation"] or "",
                    "-" if depth is None else depth,
                    item["message"] or "",
                ),
            )

        _clear(uvm_tree)
        for message in current["uvm_messages"]:
            uvm_tree.insert(
                "",
                "end",
                values=(
                    message["severity"],
                    message["report_id"] or "",
                    message["component"] or "",
                    message["time_text"] or "",
                    message["log_line"],
                    message["message"] or "",
                ),
            )

        _clear(evidence_tree)
        assertions = current["assertions"]
        evidence_tree.insert(
            "",
            "end",
            values=(
                "Assertions",
                "FAIL"
                if assertions["failed"]
                else ("PASS" if assertions["total"] else "NOT_PRESENT"),
                f"{assertions['passed']}/{assertions['total']} passed; "
                f"{assertions['failed']} failed",
                "normalized assertion database",
            ),
        )

        if coverage is None:
            evidence_tree.insert("", "end", values=("Coverage", "NOT_PRESENT", "-", "-"))
        else:
            evidence_tree.insert(
                "",
                "end",
                values=(
                    "Coverage",
                    "PRESENT",
                    f"{coverage['percent']:.1f}% · {coverage['kind']} · "
                    f"{coverage['simulator']}",
                    coverage["snapshot_id"],
                ),
            )

        formal = current["latest_formal"]
        if formal is None:
            evidence_tree.insert("", "end", values=("Formal", "NOT_PRESENT", "-", "-"))
        else:
            details = f"{formal['mode']} · {formal['property_count']} properties"
            evidence_tree.insert(
                "",
                "end",
                values=("Formal", formal["status"], details, formal["snapshot_id"]),
            )

        uvm = current["latest_uvm"]
        if uvm is None:
            evidence_tree.insert("", "end", values=("UVM", "NOT_PRESENT", "-", "-"))
        else:
            details = (
                f"test={uvm['test_name'] or '(unknown)'} · "
                f"errors={uvm['error_count']} · fatals={uvm['fatal_count']}"
            )
            evidence_tree.insert(
                "",
                "end",
                values=("UVM", uvm["status"], details, uvm["snapshot_id"]),
            )

        status_text.set(
            f"{project.simulator} · top={project.top} · "
            f"showing {len(current['recent_runs'])} runs · "
            f"{design['summary']['files']} source files · "
            f"{design['summary']['instances']} instances"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

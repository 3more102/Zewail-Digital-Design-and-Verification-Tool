from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.design_index import build_design_index
from zddv.storage import (
    assertion_statistics,
    list_coverage_score_snapshots,
    list_coverage_snapshots,
    list_formal_result_snapshots,
    list_run_records,
    list_uvm_log_snapshots,
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


def _persisted_elaboration(project: ProjectConfig) -> dict[str, Any]:
    """Read an existing elaborated index without running a simulator or writing files."""
    path = (project.root / ".zddv" / "design" / "elaborated.json").resolve()
    base = {
        "status": "NOT_PRESENT",
        "path": str(path),
        "created_at": None,
        "top": project.top,
        "simulator": None,
        "simulator_version": None,
        "source_format": None,
        "modules": [],
        "instances": [],
    }
    if not path.exists():
        return base

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**base, "status": "INVALID", "error": str(exc)}

    if not isinstance(payload, dict):
        return {
            **base,
            "status": "INVALID",
            "error": "Elaborated hierarchy JSON must contain an object.",
        }

    modules = payload.get("modules")
    instances = payload.get("instances")
    if not isinstance(modules, list) or not isinstance(instances, list):
        return {
            **base,
            "status": "INVALID",
            "error": "Elaborated hierarchy JSON is missing modules/instances lists.",
        }

    return {
        **base,
        "status": "PRESENT",
        "created_at": payload.get("created_at"),
        "top": payload.get("top") or project.top,
        "simulator": payload.get("simulator"),
        "simulator_version": payload.get("simulator_version"),
        "source_format": payload.get("source_format"),
        "modules": modules,
        "instances": instances,
    }


def build_desktop_snapshot(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Compose read-only Debug Studio state from evidence and current source files."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    runs = list_run_records(project, limit=limit)
    formal_rows = list_formal_result_snapshots(project, limit=1)
    uvm_rows = list_uvm_log_snapshots(project, limit=1)

    return {
        "project": {
            "name": project.name,
            "root": str(project.root),
            "top": project.top,
            "simulator": project.simulator,
        },
        "policy": {
            "display_only": True,
            "executes_verification": False,
            "invokes_ai": False,
            "applies_generated_artifacts": False,
        },
        "stats": run_statistics(project),
        "assertions": assertion_statistics(project),
        "recent_runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": formal_rows[0] if formal_rows else None,
        "latest_uvm": uvm_rows[0] if uvm_rows else None,
        "design": build_design_index(project),
        "persisted_elaboration": _persisted_elaboration(project),
    }


def _resolved_source_path(project: ProjectConfig, display_path: str) -> Path:
    path = Path(display_path)
    if not path.is_absolute():
        path = project.root / path
    return path.resolve()


def launch_desktop_gui(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Launch the read-only Tk Debug Studio and return the last shown snapshot."""
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
    window.geometry("1260x820")
    window.minsize(940, 620)

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
    source_tab = ttk.Frame(notebook, padding=8)
    hierarchy_tab = ttk.Frame(notebook, padding=8)
    notebook.add(run_tab, text="Recent Runs")
    notebook.add(failure_tab, text="Failure Groups")
    notebook.add(evidence_tab, text="Evidence")
    notebook.add(source_tab, text="Source")
    notebook.add(hierarchy_tab, text="Hierarchy")

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

    source_split = ttk.Panedwindow(source_tab, orient="horizontal")
    source_split.pack(fill="both", expand=True)
    source_left = ttk.Frame(source_split)
    source_right = ttk.Frame(source_split)
    source_split.add(source_left)
    source_split.add(source_right)

    source_tree = ttk.Treeview(
        source_left,
        columns=("kind", "lines", "sha"),
        show="tree headings",
    )
    source_tree.heading("#0", text="Source / Unit")
    source_tree.heading("kind", text="Kind")
    source_tree.heading("lines", text="Lines")
    source_tree.heading("sha", text="SHA-256")
    source_tree.column("#0", width=330, anchor="w")
    source_tree.column("kind", width=100, anchor="w")
    source_tree.column("lines", width=90, anchor="w")
    source_tree.column("sha", width=120, anchor="w")
    source_tree.pack(fill="both", expand=True)

    source_location_text = tk.StringVar(value="Select a source file or design unit.")
    ttk.Label(source_right, textvariable=source_location_text).grid(
        row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6)
    )
    source_text = tk.Text(source_right, wrap="none")
    source_y = ttk.Scrollbar(source_right, orient="vertical", command=source_text.yview)
    source_x = ttk.Scrollbar(source_right, orient="horizontal", command=source_text.xview)
    source_text.configure(yscrollcommand=source_y.set, xscrollcommand=source_x.set)
    source_text.grid(row=1, column=0, sticky="nsew")
    source_y.grid(row=1, column=1, sticky="ns")
    source_x.grid(row=2, column=0, sticky="ew")
    source_right.rowconfigure(1, weight=1)
    source_right.columnconfigure(0, weight=1)
    source_text.configure(state="disabled")

    hierarchy_book = ttk.Notebook(hierarchy_tab)
    hierarchy_book.pack(fill="both", expand=True)
    source_hierarchy_tab = ttk.Frame(hierarchy_book, padding=6)
    elaborated_hierarchy_tab = ttk.Frame(hierarchy_book, padding=6)
    hierarchy_book.add(source_hierarchy_tab, text="Source hierarchy")
    hierarchy_book.add(elaborated_hierarchy_tab, text="Persisted elaboration")

    source_hierarchy_tree = ttk.Treeview(
        source_hierarchy_tab,
        columns=("type", "source", "line", "status"),
        show="tree headings",
    )
    source_hierarchy_tree.heading("#0", text="Instance")
    source_hierarchy_tree.heading("type", text="Type")
    source_hierarchy_tree.heading("source", text="Source")
    source_hierarchy_tree.heading("line", text="Line")
    source_hierarchy_tree.heading("status", text="Status")
    source_hierarchy_tree.column("#0", width=280, anchor="w")
    source_hierarchy_tree.column("type", width=180, anchor="w")
    source_hierarchy_tree.column("source", width=360, anchor="w")
    source_hierarchy_tree.column("line", width=70, anchor="w")
    source_hierarchy_tree.column("status", width=120, anchor="w")
    source_hierarchy_tree.pack(fill="both", expand=True)

    elaborated_status = tk.StringVar(value="")
    ttk.Label(elaborated_hierarchy_tab, textvariable=elaborated_status).pack(
        fill="x", pady=(0, 6)
    )
    elaborated_tree = ttk.Treeview(
        elaborated_hierarchy_tab,
        columns=("module", "source", "line"),
        show="tree headings",
    )
    elaborated_tree.heading("#0", text="Elaborated instance")
    elaborated_tree.heading("module", text="Module")
    elaborated_tree.heading("source", text="Source")
    elaborated_tree.heading("line", text="Line")
    elaborated_tree.column("#0", width=360, anchor="w")
    elaborated_tree.column("module", width=220, anchor="w")
    elaborated_tree.column("source", width=420, anchor="w")
    elaborated_tree.column("line", width=70, anchor="w")
    elaborated_tree.pack(fill="both", expand=True)

    footer = ttk.Frame(container, padding=(0, 10, 0, 0))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        text=(
            "Read-only viewer: refresh reads persisted verification evidence and current "
            "configured source files; it does not run simulation/elaboration, invoke AI, "
            "or write/apply project artifacts."
        ),
    ).pack(side="left")

    current: dict[str, Any] = {}
    source_locations: dict[str, dict[str, Any]] = {}
    source_hierarchy_locations: dict[str, dict[str, Any]] = {}
    elaborated_locations: dict[str, dict[str, Any]] = {}

    def _clear(tree) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _show_source_location(location: dict[str, Any] | None) -> None:
        if not location:
            return
        display_path = location.get("file") or location.get("path")
        if not display_path:
            return

        try:
            path = _resolved_source_path(project, str(display_path))
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            source_location_text.set(f"{display_path} — {exc}")
            return

        line_value = location.get("line")
        try:
            line = max(1, int(line_value)) if line_value is not None else 1
        except (TypeError, ValueError):
            line = 1

        source_text.configure(state="normal")
        source_text.delete("1.0", "end")
        source_text.insert("1.0", text)
        source_text.mark_set("insert", f"{line}.0")
        source_text.see(f"{line}.0")
        source_text.configure(state="disabled")
        source_location_text.set(f"{display_path}:{line}")
        notebook.select(source_tab)

    def _select_mapped(tree, mapping: dict[str, dict[str, Any]]) -> None:
        selected = tree.selection()
        if not selected:
            return
        _show_source_location(mapping.get(selected[0]))

    source_tree.bind(
        "<<TreeviewSelect>>",
        lambda _event: _select_mapped(source_tree, source_locations),
    )
    source_hierarchy_tree.bind(
        "<<TreeviewSelect>>",
        lambda _event: _select_mapped(
            source_hierarchy_tree,
            source_hierarchy_locations,
        ),
    )
    elaborated_tree.bind(
        "<<TreeviewSelect>>",
        lambda _event: _select_mapped(elaborated_tree, elaborated_locations),
    )

    def _populate_source(design: dict[str, Any]) -> None:
        _clear(source_tree)
        source_locations.clear()

        units_by_file: dict[str, list[dict[str, Any]]] = {}
        for unit in design.get("units", []):
            units_by_file.setdefault(str(unit["file"]), []).append(unit)

        for file_index, item in enumerate(design.get("files", [])):
            display_path = str(item["path"])
            file_iid = f"source-file-{file_index}"
            source_tree.insert(
                "",
                "end",
                iid=file_iid,
                text=display_path,
                values=("file", item["lines"], str(item["sha256"])[:12]),
            )
            source_locations[file_iid] = {"file": display_path, "line": 1}

            for unit_index, unit in enumerate(units_by_file.get(display_path, [])):
                unit_iid = f"source-unit-{file_index}-{unit_index}"
                line_range = (
                    str(unit["line"])
                    if unit["line"] == unit["end_line"]
                    else f"{unit['line']}-{unit['end_line']}"
                )
                source_tree.insert(
                    file_iid,
                    "end",
                    iid=unit_iid,
                    text=str(unit["name"]),
                    values=(unit["kind"], line_range, ""),
                )
                source_locations[unit_iid] = {
                    "file": display_path,
                    "line": unit["line"],
                }

    def _populate_source_hierarchy(design: dict[str, Any]) -> None:
        _clear(source_hierarchy_tree)
        source_hierarchy_locations.clear()
        sequence = [0]

        def add_node(parent: str, node: dict[str, Any]) -> None:
            iid = f"source-hierarchy-{sequence[0]}"
            sequence[0] += 1
            resolved = bool(node.get("resolved", True))
            recursive = bool(node.get("recursive"))
            status = (
                "UNRESOLVED"
                if not resolved
                else ("RECURSIVE" if recursive else "RESOLVED")
            )
            file_path = node.get("file") or "-"
            line = node.get("line")
            source_hierarchy_tree.insert(
                parent,
                "end",
                iid=iid,
                text=str(node.get("instance") or "?"),
                values=(
                    node.get("type") or "?",
                    file_path,
                    "-" if line is None else line,
                    status,
                ),
                open=True,
            )
            if node.get("file"):
                source_hierarchy_locations[iid] = {
                    "file": node["file"],
                    "line": node.get("line") or 1,
                }
            for child in node.get("children", []):
                add_node(iid, child)

        hierarchy = design.get("hierarchy")
        if isinstance(hierarchy, dict):
            add_node("", hierarchy)

    def _populate_elaborated(elaboration: dict[str, Any]) -> None:
        _clear(elaborated_tree)
        elaborated_locations.clear()

        status = elaboration["status"]
        if status == "NOT_PRESENT":
            elaborated_status.set(
                "No persisted elaborated hierarchy. Run zddv elaborate explicitly "
                "outside the GUI to create one."
            )
            return
        if status == "INVALID":
            elaborated_status.set(
                f"Persisted elaborated hierarchy is invalid: "
                f"{elaboration.get('error', 'unknown error')}"
            )
            return

        label = (
            f"Persisted only · "
            f"{elaboration.get('simulator_version') or elaboration.get('simulator') or 'simulator unknown'}"
        )
        if elaboration.get("created_at"):
            label += f" · {elaboration['created_at']}"
        elaborated_status.set(label)

        path_to_iid: dict[str, str] = {}
        instances = sorted(
            elaboration.get("instances", []),
            key=lambda item: (
                str(item.get("path") or "").count("."),
                str(item.get("path") or ""),
            ),
        )
        for index, item in enumerate(instances):
            path = str(item.get("path") or item.get("name") or f"instance-{index}")
            parent_path = path.rsplit(".", 1)[0] if "." in path else ""
            parent_iid = path_to_iid.get(parent_path, "")
            iid = f"elaborated-{index}"
            location = (
                item.get("location")
                if isinstance(item.get("location"), dict)
                else {}
            )
            source_path = location.get("path") or "-"
            line = location.get("line")
            elaborated_tree.insert(
                parent_iid,
                "end",
                iid=iid,
                text=path,
                values=(
                    item.get("module") or "?",
                    source_path,
                    "-" if line is None else line,
                ),
                open=True,
            )
            path_to_iid[path] = iid
            if location.get("path"):
                elaborated_locations[iid] = {
                    "path": location["path"],
                    "line": location.get("line") or 1,
                }

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

        design = current["design"]
        _populate_source(design)
        _populate_source_hierarchy(design)
        _populate_elaborated(current["persisted_elaboration"])

        design_summary = design["summary"]
        status_text.set(
            f"{project.simulator} · top={project.top} · "
            f"{len(current['recent_runs'])} runs · "
            f"{design_summary['files']} source files · "
            f"{design_summary['instances']} source instances"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

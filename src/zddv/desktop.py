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


def _project_source_map(project: ProjectConfig) -> dict[str, Path]:
    result: dict[str, Path] = {}
    root = project.root.resolve()
    for source in project.source_files():
        resolved = source.resolve()
        try:
            label = str(resolved.relative_to(root))
        except ValueError:
            label = str(resolved)
        result[label] = resolved
    return result


def read_source_text(project: ProjectConfig, source_path: str) -> dict[str, Any]:
    """Read one configured project source without allowing arbitrary paths."""
    source_map = _project_source_map(project)
    path = source_map.get(source_path)
    if path is None:
        raise ValueError(f"Source is not part of the configured ZDDV project: {source_path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": source_path,
        "text": text,
        "lines": text.count("\n") + (0 if text.endswith("\n") else 1),
    }


def _load_elaborated_evidence(project: ProjectConfig) -> dict[str, Any]:
    path = (project.root / ".zddv" / "design" / "elaborated.json").resolve()
    base = {
        "path": str(path),
        "status": "NOT_PRESENT",
        "reason": None,
        "modules": [],
        "instances": [],
        "summary": {"modules": 0, "instances": 0},
    }
    if not path.exists():
        return base

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            **base,
            "status": "INVALID",
            "reason": f"Could not parse persisted elaboration evidence: {exc}",
        }

    mismatches: list[str] = []
    for field, expected in (
        ("project", project.name),
        ("top", project.top),
        ("simulator", project.simulator),
    ):
        actual = payload.get(field)
        if actual != expected:
            mismatches.append(f"{field}={actual!r}, expected {expected!r}")

    if mismatches:
        return {
            **base,
            "status": "STALE",
            "reason": "; ".join(mismatches),
            "source_format": payload.get("source_format"),
            "simulator_version": payload.get("simulator_version"),
        }

    modules = payload.get("modules")
    instances = payload.get("instances")
    if not isinstance(modules, list) or not isinstance(instances, list):
        return {
            **base,
            "status": "INVALID",
            "reason": "Persisted elaboration evidence is missing module/instance lists.",
        }

    return {
        **base,
        "status": "PRESENT",
        "source_format": payload.get("source_format"),
        "simulator_version": payload.get("simulator_version"),
        "modules": modules,
        "instances": instances,
        "summary": {
            "modules": len(modules),
            "instances": len(instances),
        },
    }


def flatten_source_hierarchy(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the normalized source hierarchy for GUI and headless tests."""
    rows: list[dict[str, Any]] = []

    def visit(item: dict[str, Any], depth: int) -> None:
        rows.append(
            {
                "path": item.get("path"),
                "instance": item.get("instance"),
                "type": item.get("type"),
                "kind": item.get("kind"),
                "file": item.get("file"),
                "line": item.get("line"),
                "resolved": bool(item.get("resolved", True)),
                "recursive": bool(item.get("recursive", False)),
                "depth": depth,
            }
        )
        for child in item.get("children", []):
            visit(child, depth + 1)

    visit(node, 0)
    return rows


def build_desktop_snapshot(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Compose display-only Debug Studio state from existing project/evidence data."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    runs = list_run_records(project, limit=limit)
    formal_rows = list_formal_result_snapshots(project, limit=1)
    uvm_rows = list_uvm_log_snapshots(project, limit=1)
    design = build_design_index(project)

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
            "writes_project_artifacts": False,
        },
        "stats": run_statistics(project),
        "assertions": assertion_statistics(project),
        "recent_runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": formal_rows[0] if formal_rows else None,
        "latest_uvm": uvm_rows[0] if uvm_rows else None,
        "design": {
            "summary": design["summary"],
            "files": design["files"],
            "units": design["units"],
            "duplicates": design["duplicates"],
            "source_hierarchy": design["hierarchy"],
            "source_hierarchy_rows": flatten_source_hierarchy(design["hierarchy"]),
            "elaborated": _load_elaborated_evidence(project),
        },
    }


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
    window.geometry("1320x820")
    window.minsize(980, 640)

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

    source_pane = ttk.Panedwindow(source_tab, orient="horizontal")
    source_pane.pack(fill="both", expand=True)
    source_left = ttk.Frame(source_pane)
    source_right = ttk.Frame(source_pane)
    source_pane.add(source_left, weight=1)
    source_pane.add(source_right, weight=3)

    source_tree = ttk.Treeview(
        source_left,
        columns=("kind", "line"),
        show="tree headings",
    )
    source_tree.heading("#0", text="Source / unit")
    source_tree.heading("kind", text="Kind")
    source_tree.heading("line", text="Line")
    source_tree.column("#0", width=300, anchor="w")
    source_tree.column("kind", width=100, anchor="w")
    source_tree.column("line", width=70, anchor="e")
    source_tree.pack(fill="both", expand=True)

    source_text = tk.Text(source_right, wrap="none", state="disabled")
    source_y = ttk.Scrollbar(source_right, orient="vertical", command=source_text.yview)
    source_x = ttk.Scrollbar(source_right, orient="horizontal", command=source_text.xview)
    source_text.configure(yscrollcommand=source_y.set, xscrollcommand=source_x.set)
    source_text.grid(row=0, column=0, sticky="nsew")
    source_y.grid(row=0, column=1, sticky="ns")
    source_x.grid(row=1, column=0, sticky="ew")
    source_right.rowconfigure(0, weight=1)
    source_right.columnconfigure(0, weight=1)
    source_text.tag_configure("selected_line", background="#fff3b0")

    hierarchy_tree = ttk.Treeview(
        hierarchy_tab,
        columns=("type", "source", "status"),
        show="tree headings",
    )
    hierarchy_tree.heading("#0", text="Instance")
    hierarchy_tree.heading("type", text="Type / module")
    hierarchy_tree.heading("source", text="Source")
    hierarchy_tree.heading("status", text="Evidence")
    hierarchy_tree.column("#0", width=330, anchor="w")
    hierarchy_tree.column("type", width=220, anchor="w")
    hierarchy_tree.column("source", width=380, anchor="w")
    hierarchy_tree.column("status", width=120, anchor="w")
    hierarchy_tree.pack(fill="both", expand=True)

    footer = ttk.Frame(container, padding=(0, 10, 0, 0))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        text=(
            "Display-only viewer: refresh reads persisted evidence and configured "
            "sources; it does not run verification, invoke AI, or write project artifacts."
        ),
    ).pack(side="left")

    current: dict[str, Any] = {}
    source_items: dict[str, tuple[str, int | None]] = {}

    def _clear(tree) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _show_source(_event=None) -> None:
        selection = source_tree.selection()
        if not selection:
            return
        metadata = source_items.get(selection[0])
        if metadata is None:
            return
        path, line = metadata
        try:
            payload = read_source_text(project, path)
        except (OSError, ValueError) as exc:
            text = f"Unable to read {path}: {exc}\n"
            line = None
        else:
            text = payload["text"]

        source_text.configure(state="normal")
        source_text.delete("1.0", "end")
        source_text.insert("1.0", text)
        source_text.tag_remove("selected_line", "1.0", "end")
        if line is not None and line >= 1:
            source_text.tag_add("selected_line", f"{line}.0", f"{line}.end")
            source_text.see(f"{line}.0")
        source_text.configure(state="disabled")

    source_tree.bind("<<TreeviewSelect>>", _show_source)

    def _populate_source(design: dict[str, Any]) -> None:
        _clear(source_tree)
        source_items.clear()
        units_by_file: dict[str, list[dict[str, Any]]] = {}
        for unit in design["units"]:
            units_by_file.setdefault(str(unit["file"]), []).append(unit)

        for file_row in design["files"]:
            path = str(file_row["path"])
            file_id = source_tree.insert(
                "",
                "end",
                text=path,
                values=("file", ""),
                open=True,
            )
            source_items[file_id] = (path, None)
            for unit in units_by_file.get(path, []):
                unit_id = source_tree.insert(
                    file_id,
                    "end",
                    text=str(unit["name"]),
                    values=(unit["kind"], unit["line"]),
                )
                source_items[unit_id] = (path, int(unit["line"]))

    def _source_location(item: dict[str, Any]) -> str:
        path = item.get("file")
        line = item.get("line")
        if path is None:
            return "-"
        return f"{path}:{line}" if line is not None else str(path)

    def _populate_hierarchy(design: dict[str, Any]) -> None:
        _clear(hierarchy_tree)

        source_root = hierarchy_tree.insert(
            "",
            "end",
            text="Source-level hierarchy",
            values=("", "", "SOURCE"),
            open=True,
        )

        def add_source(parent: str, item: dict[str, Any]) -> None:
            evidence = "SOURCE"
            if not item.get("resolved", True):
                evidence = "UNRESOLVED"
            elif item.get("recursive"):
                evidence = "RECURSIVE"
            item_id = hierarchy_tree.insert(
                parent,
                "end",
                text=str(item.get("instance") or "?"),
                values=(
                    item.get("type") or "?",
                    _source_location(item),
                    evidence,
                ),
                open=True,
            )
            for child in item.get("children", []):
                add_source(item_id, child)

        add_source(source_root, design["source_hierarchy"])

        elaborated = design["elaborated"]
        elaborated_root = hierarchy_tree.insert(
            "",
            "end",
            text=f"Elaborated hierarchy ({elaborated['status']})",
            values=(
                elaborated.get("source_format") or "",
                elaborated.get("path") or "",
                elaborated["status"],
            ),
            open=True,
        )
        if elaborated["status"] != "PRESENT":
            if elaborated.get("reason"):
                hierarchy_tree.insert(
                    elaborated_root,
                    "end",
                    text=str(elaborated["reason"]),
                    values=("", "", elaborated["status"]),
                )
            return

        instance_ids: dict[str, str] = {}
        instances = sorted(
            elaborated["instances"],
            key=lambda item: (
                str(item.get("path") or "").count("."),
                str(item.get("path") or ""),
            ),
        )
        for item in instances:
            path = str(item.get("path") or "")
            if not path:
                continue
            parent_path = path.rsplit(".", 1)[0] if "." in path else None
            parent = instance_ids.get(parent_path, elaborated_root)
            location = item.get("location") or {}
            source = location.get("path")
            if source and location.get("line") is not None:
                source = f"{source}:{location['line']}"
            instance_id = hierarchy_tree.insert(
                parent,
                "end",
                text=str(item.get("name") or path.rsplit(".", 1)[-1]),
                values=(
                    item.get("module") or "?",
                    source or "-",
                    "ELABORATED",
                ),
                open=True,
            )
            instance_ids[path] = instance_id

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

        elaborated = current["design"]["elaborated"]
        evidence_tree.insert(
            "",
            "end",
            values=(
                "Elaboration",
                elaborated["status"],
                (
                    f"{elaborated['summary']['modules']} modules · "
                    f"{elaborated['summary']['instances']} instances"
                    if elaborated["status"] == "PRESENT"
                    else (elaborated.get("reason") or "No persisted elaboration evidence")
                ),
                elaborated["path"],
            ),
        )

        _populate_source(current["design"])
        _populate_hierarchy(current["design"])
        status_text.set(
            f"{project.simulator} · top={project.top} · "
            f"{current['design']['summary']['files']} source files · "
            f"showing {len(current['recent_runs'])} runs"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

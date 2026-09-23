from __future__ import annotations

import json
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


def _flatten_source_hierarchy(
    node: dict[str, Any],
    units_by_name: dict[str, dict[str, Any]],
    *,
    parent_path: str | None = None,
) -> list[dict[str, Any]]:
    unit = units_by_name.get(str(node.get("type") or ""))
    row = {
        "path": str(node.get("path") or ""),
        "parent_path": parent_path,
        "instance": str(node.get("instance") or ""),
        "type": str(node.get("type") or ""),
        "resolved": bool(node.get("resolved", True)),
        "recursive": bool(node.get("recursive", False)),
        "file": None if unit is None else unit.get("file"),
        "line": None if unit is None else unit.get("line"),
    }
    rows = [row]
    for child in node.get("children", []):
        rows.extend(
            _flatten_source_hierarchy(
                child,
                units_by_name,
                parent_path=row["path"] or parent_path,
            )
        )
    return rows


def _load_persisted_elaborated_hierarchy(project: ProjectConfig) -> dict[str, Any]:
    path = project.root / ".zddv" / "design" / "elaborated.json"
    if not path.exists():
        return {"status": "NOT_PRESENT", "path": str(path), "instances": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "INVALID",
            "path": str(path),
            "error": str(exc),
            "instances": [],
        }
    if not isinstance(payload, dict) or not isinstance(payload.get("instances", []), list):
        return {
            "status": "INVALID",
            "path": str(path),
            "error": "elaborated hierarchy payload must contain an instances list",
            "instances": [],
        }
    return {
        "status": "PRESENT",
        "path": str(path),
        "created_at": payload.get("created_at"),
        "simulator": payload.get("simulator"),
        "simulator_version": payload.get("simulator_version"),
        "source_format": payload.get("source_format"),
        "summary": payload.get("summary") or {},
        "instances": payload.get("instances", []),
    }


def _build_design_view(project: ProjectConfig) -> dict[str, Any]:
    index = build_design_index(project)
    units_by_name: dict[str, dict[str, Any]] = {}
    for unit in index["units"]:
        units_by_name.setdefault(unit["name"], unit)
    return {
        "summary": index["summary"],
        "files": index["files"],
        "units": [
            {
                "kind": unit["kind"],
                "name": unit["name"],
                "file": unit["file"],
                "line": unit["line"],
                "end_line": unit["end_line"],
            }
            for unit in index["units"]
        ],
        "hierarchy": _flatten_source_hierarchy(index["hierarchy"], units_by_name),
        "elaborated": _load_persisted_elaborated_hierarchy(project),
    }


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
        "design": _build_design_view(project),
    }


def launch_desktop_gui(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Launch a read-only Tk desktop dashboard and return the last shown snapshot."""
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
    design_tab = ttk.Frame(notebook, padding=8)
    notebook.add(run_tab, text="Recent Runs")
    notebook.add(failure_tab, text="Failure Groups")
    notebook.add(evidence_tab, text="Evidence")
    notebook.add(design_tab, text="Design")

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


    design_notebook = ttk.Notebook(design_tab)
    design_notebook.pack(fill="both", expand=True)
    units_tab = ttk.Frame(design_notebook, padding=6)
    source_hierarchy_tab = ttk.Frame(design_notebook, padding=6)
    elaborated_tab = ttk.Frame(design_notebook, padding=6)
    design_notebook.add(units_tab, text="Source Units")
    design_notebook.add(source_hierarchy_tab, text="Source Hierarchy")
    design_notebook.add(elaborated_tab, text="Persisted Elaborated")

    unit_columns = ("kind", "name", "file", "line", "end_line")
    unit_tree = ttk.Treeview(units_tab, columns=unit_columns, show="headings")
    for column, title, width in (
        ("kind", "Kind", 100),
        ("name", "Unit", 180),
        ("file", "Source file", 420),
        ("line", "Line", 80),
        ("end_line", "End", 80),
    ):
        unit_tree.heading(column, text=title)
        unit_tree.column(column, width=width, anchor="w")
    unit_tree.pack(fill="both", expand=True)

    hierarchy_tree = ttk.Treeview(
        source_hierarchy_tab,
        columns=("type", "source", "path"),
        show="tree headings",
    )
    hierarchy_tree.heading("#0", text="Instance")
    hierarchy_tree.column("#0", width=220, anchor="w")
    for column, title, width in (
        ("type", "Type", 180),
        ("source", "Source", 360),
        ("path", "Hierarchy path", 360),
    ):
        hierarchy_tree.heading(column, text=title)
        hierarchy_tree.column(column, width=width, anchor="w")
    hierarchy_tree.pack(fill="both", expand=True)

    elaborated_columns = ("path", "module", "source")
    elaborated_tree = ttk.Treeview(
        elaborated_tab,
        columns=elaborated_columns,
        show="headings",
    )
    for column, title, width in (
        ("path", "Hierarchy path", 420),
        ("module", "Module", 220),
        ("source", "Source", 360),
    ):
        elaborated_tree.heading(column, text=title)
        elaborated_tree.column(column, width=width, anchor="w")
    elaborated_tree.pack(fill="both", expand=True)

    footer = ttk.Frame(container, padding=(0, 10, 0, 0))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        text=(
            "Display-only viewer: refresh reads persisted evidence; it does not run "
            "verification, invoke AI, or apply generated artifacts."
        ),
    ).pack(side="left")

    current: dict[str, Any] = {}

    def _clear(tree) -> None:
        for item in tree.get_children():
            tree.delete(item)

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

        _clear(unit_tree)
        for row in current["design"]["units"]:
            unit_tree.insert(
                "",
                "end",
                values=(
                    row["kind"],
                    row["name"],
                    row["file"],
                    row["line"],
                    row["end_line"],
                ),
            )

        _clear(hierarchy_tree)
        hierarchy_items: dict[str, str] = {}
        for row in current["design"]["hierarchy"]:
            parent_item = hierarchy_items.get(row["parent_path"] or "", "")
            state = "UNRESOLVED" if not row["resolved"] else ("RECURSIVE" if row["recursive"] else "")
            source = "-"
            if row["file"]:
                source = f"{row['file']}:{row['line']}" if row["line"] else str(row["file"])
            label = row["instance"] + (f" [{state}]" if state else "")
            item = hierarchy_tree.insert(
                parent_item,
                "end",
                text=label,
                values=(row["type"], source, row["path"]),
                open=True,
            )
            hierarchy_items[row["path"]] = item

        _clear(elaborated_tree)
        elaborated = current["design"]["elaborated"]
        if elaborated["status"] == "PRESENT":
            for row in elaborated["instances"]:
                location = row.get("location") or {}
                source = "-"
                if location.get("path"):
                    source = str(location["path"])
                    if location.get("line"):
                        source += f":{location['line']}"
                elaborated_tree.insert(
                    "",
                    "end",
                    values=(row.get("path") or "-", row.get("module") or "-", source),
                )
        else:
            elaborated_tree.insert(
                "",
                "end",
                values=(elaborated["status"], "-", elaborated.get("error") or elaborated["path"]),
            )

        status_text.set(
            f"{project.simulator} · top={project.top} · "
            f"showing {len(current['recent_runs'])} runs"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

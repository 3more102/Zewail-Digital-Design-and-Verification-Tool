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


def _existing_elaborated_index(project: ProjectConfig) -> dict[str, Any] | None:
    """Read a previously captured elaborated hierarchy without invoking a simulator."""
    path = (project.root / ".zddv" / "design" / "elaborated.json").resolve()
    if not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "INVALID",
            "path": str(path),
            "error": str(exc),
        }

    if not isinstance(payload, dict):
        return {
            "status": "INVALID",
            "path": str(path),
            "error": "Elaborated hierarchy root must be a JSON object.",
        }

    mismatches = []
    for field, expected in (
        ("project", project.name),
        ("top", project.top),
        ("simulator", project.simulator),
    ):
        actual = payload.get(field)
        if actual != expected:
            mismatches.append(
                {
                    "field": field,
                    "expected": expected,
                    "actual": actual,
                }
            )

    if mismatches:
        return {
            "status": "MISMATCH",
            "path": str(path),
            "mismatches": mismatches,
            "index": payload,
        }

    return {
        "status": "AVAILABLE",
        "path": str(path),
        "index": payload,
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
    source_index = build_design_index(project)

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
            "executes_elaboration": False,
            "edits_sources": False,
        },
        "stats": run_statistics(project),
        "assertions": assertion_statistics(project),
        "recent_runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": formal_rows[0] if formal_rows else None,
        "latest_uvm": uvm_rows[0] if uvm_rows else None,
        "design": {
            "source_index": source_index,
            "elaborated": _existing_elaborated_index(project),
        },
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
    source_tab = ttk.Frame(notebook, padding=8)
    hierarchy_tab = ttk.Frame(notebook, padding=8)
    notebook.add(run_tab, text="Recent Runs")
    notebook.add(failure_tab, text="Failure Groups")
    notebook.add(evidence_tab, text="Evidence")
    notebook.add(source_tab, text="Sources")
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

    source_tree = ttk.Treeview(
        source_tab,
        columns=("kind", "location", "lines", "bytes", "sha"),
        show="tree headings",
    )
    source_tree.heading("#0", text="Source / design unit")
    source_tree.column("#0", width=260, anchor="w")
    for column, title, width in (
        ("kind", "Kind", 110),
        ("location", "Location", 300),
        ("lines", "Lines", 90),
        ("bytes", "Bytes", 100),
        ("sha", "SHA-256", 240),
    ):
        source_tree.heading(column, text=title)
        source_tree.column(column, width=width, anchor="w")
    source_tree.pack(fill="both", expand=True)

    hierarchy_tree = ttk.Treeview(
        hierarchy_tab,
        columns=("type", "kind", "source", "line", "status"),
        show="tree headings",
    )
    hierarchy_tree.heading("#0", text="Instance")
    hierarchy_tree.column("#0", width=280, anchor="w")
    for column, title, width in (
        ("type", "Type / module", 200),
        ("kind", "View", 130),
        ("source", "Source", 300),
        ("line", "Line", 80),
        ("status", "Status", 140),
    ):
        hierarchy_tree.heading(column, text=title)
        hierarchy_tree.column(column, width=width, anchor="w")
    hierarchy_tree.pack(fill="both", expand=True)

    footer = ttk.Frame(container, padding=(0, 10, 0, 0))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        text=(
            "Display-only viewer: refresh reads verification evidence and project sources; "
            "it does not run verification/elaboration, invoke AI, edit sources, or apply "
            "generated artifacts."
        ),
    ).pack(side="left")

    current: dict[str, Any] = {}

    def _clear(tree) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _insert_source_hierarchy(parent, item: dict[str, Any]) -> None:
        resolved = bool(item.get("resolved", True))
        recursive = bool(item.get("recursive", False))
        status = "UNRESOLVED" if not resolved else ("RECURSIVE" if recursive else "RESOLVED")
        node = hierarchy_tree.insert(
            parent,
            "end",
            text=item.get("instance") or item.get("path") or "?",
            open=True,
            values=(
                item.get("type") or "?",
                "source",
                item.get("file") or "-",
                item.get("line") or "-",
                status,
            ),
        )
        for child in item.get("children", []):
            _insert_source_hierarchy(node, child)

    def _populate_design(snapshot: dict[str, Any]) -> None:
        _clear(source_tree)
        _clear(hierarchy_tree)

        source_index = snapshot["design"]["source_index"]
        file_nodes: dict[str, str] = {}
        for row in source_index["files"]:
            node = source_tree.insert(
                "",
                "end",
                text=row["path"],
                open=True,
                values=(
                    "file",
                    row["path"],
                    row["lines"],
                    row["bytes"],
                    row["sha256"],
                ),
            )
            file_nodes[row["path"]] = node

        for unit in source_index["units"]:
            parent = file_nodes.get(unit["file"], "")
            source_tree.insert(
                parent,
                "end",
                text=unit["name"],
                values=(
                    unit["kind"],
                    f"{unit['file']}:{unit['line']}-{unit['end_line']}",
                    "-",
                    "-",
                    "-",
                ),
            )

        source_root = hierarchy_tree.insert(
            "",
            "end",
            text="Source hierarchy",
            open=True,
            values=(source_index["top"], "source", "-", "-", "CURRENT"),
        )
        _insert_source_hierarchy(source_root, source_index["hierarchy"])

        elaborated = snapshot["design"]["elaborated"]
        if elaborated is None:
            hierarchy_tree.insert(
                "",
                "end",
                text="Elaborated hierarchy",
                values=("-", "elaborated", ".zddv/design/elaborated.json", "-", "NOT_CAPTURED"),
            )
            return

        status = elaborated["status"]
        if status != "AVAILABLE":
            details = elaborated.get("error") or ", ".join(
                item["field"] for item in elaborated.get("mismatches", [])
            )
            hierarchy_tree.insert(
                "",
                "end",
                text="Elaborated hierarchy",
                values=("-", "elaborated", elaborated["path"], "-", f"{status}: {details}"),
            )
            return

        index = elaborated["index"]
        elaborated_root = hierarchy_tree.insert(
            "",
            "end",
            text="Elaborated hierarchy (persisted)",
            open=True,
            values=(index.get("top") or "-", "elaborated", elaborated["path"], "-", "AVAILABLE"),
        )
        instance_nodes: dict[str, str] = {}
        instances = sorted(
            index.get("instances", []),
            key=lambda item: (
                str(item.get("path") or "").count("."),
                str(item.get("path") or ""),
            ),
        )
        for item in instances:
            path = str(item.get("path") or "")
            if not path:
                continue
            parent_path = path.rsplit(".", 1)[0] if "." in path else ""
            parent = instance_nodes.get(parent_path, elaborated_root)
            location = item.get("location") or {}
            node = hierarchy_tree.insert(
                parent,
                "end",
                text=item.get("name") or path.rsplit(".", 1)[-1],
                open=True,
                values=(
                    item.get("module") or "?",
                    "elaborated",
                    location.get("path") or "-",
                    location.get("line") or "-",
                    "PERSISTED",
                ),
            )
            instance_nodes[path] = node

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

        _populate_design(current)
        design_summary = current["design"]["source_index"]["summary"]
        status_text.set(
            f"{project.simulator} · top={project.top} · "
            f"showing {len(current['recent_runs'])} runs · "
            f"sources={design_summary['files']} · units={design_summary['units']}"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

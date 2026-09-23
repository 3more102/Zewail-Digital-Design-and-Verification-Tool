from __future__ import annotations

from datetime import datetime, timezone
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


def _metric_percent(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("percent", "score", "hit_rate", "covered"):
            candidate = value.get(key)
            if isinstance(candidate, (int, float)):
                return float(candidate)
    return None


def _latest_coverage(project: ProjectConfig) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []

    score_rows = list_coverage_score_snapshots(project, limit=1)
    if score_rows:
        row = score_rows[0]
        counts = row["by_metric_counts"]
        items: list[dict[str, Any]] = []
        for metric, value in sorted(row["by_metric"].items()):
            explicit = counts.get(metric, {})
            items.append(
                {
                    "metric": metric,
                    "percent": _metric_percent(value),
                    "covered": explicit.get("covered"),
                    "total": explicit.get("total"),
                }
            )
        candidates.append(
            {
                "snapshot_id": row["snapshot_id"],
                "created_at": row["created_at"],
                "simulator": row["simulator"],
                "kind": "score",
                "percent": float(row["score"]),
                "items": items,
            }
        )

    point_rows = list_coverage_snapshots(project, limit=1)
    if point_rows:
        row = point_rows[0]
        items = [
            {
                "metric": metric,
                "percent": float(values["hit_rate"]),
                "covered": int(values["hit"]),
                "total": int(values["total"]),
            }
            for metric, values in sorted(row["by_type"].items())
        ]
        candidates.append(
            {
                "snapshot_id": row["snapshot_id"],
                "created_at": row["created_at"],
                "simulator": row["simulator"],
                "kind": "points",
                "percent": float(row["hit_rate"]),
                "hit_points": int(row["hit_points"]),
                "total_points": int(row["total_points"]),
                "items": items,
            }
        )

    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item["created_at"]))


def _load_elaborated_index(project: ProjectConfig) -> dict[str, Any]:
    path = (project.root / ".zddv" / "design" / "elaborated.json").resolve()
    result: dict[str, Any] = {
        "status": "NOT_PRESENT",
        "path": str(path),
        "modules": [],
        "instances": [],
    }
    if not path.is_file():
        return result

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**result, "status": "INVALID", "error": str(exc)}

    if not isinstance(payload, dict):
        return {**result, "status": "INVALID", "error": "root must be a JSON object"}
    modules = payload.get("modules")
    instances = payload.get("instances")
    if not isinstance(modules, list) or not isinstance(instances, list):
        return {
            **result,
            "status": "INVALID",
            "error": "modules and instances must be arrays",
        }

    return {
        "status": "PRESENT",
        "path": str(path),
        "schema_version": payload.get("schema_version"),
        "created_at": payload.get("created_at"),
        "simulator": payload.get("simulator"),
        "simulator_version": payload.get("simulator_version"),
        "source_format": payload.get("source_format"),
        "modules": modules,
        "instances": instances,
    }


def _flatten_source_hierarchy(node: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(item: dict[str, Any]) -> None:
        rows.append(
            {
                "path": item.get("path"),
                "name": item.get("instance"),
                "module": item.get("type"),
                "file": item.get("file"),
                "line": item.get("line"),
                "kind": item.get("kind"),
                "source": "source",
                "resolved": bool(item.get("resolved", True)),
                "recursive": bool(item.get("recursive", False)),
            }
        )
        for child in item.get("children", []):
            if isinstance(child, dict):
                visit(child)

    if node:
        visit(node)
    return rows


def hierarchy_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    design = snapshot["design"]
    elaborated = design["elaborated"]
    if elaborated["status"] == "PRESENT" and elaborated["instances"]:
        rows: list[dict[str, Any]] = []
        for item in elaborated["instances"]:
            if not isinstance(item, dict):
                continue
            location = item.get("location")
            if not isinstance(location, dict):
                location = {}
            rows.append(
                {
                    "path": item.get("path"),
                    "name": item.get("name"),
                    "module": item.get("module"),
                    "file": location.get("file"),
                    "line": location.get("line"),
                    "kind": "instance",
                    "source": "elaborated",
                    "resolved": True,
                    "recursive": False,
                }
            )
        return rows
    return _flatten_source_hierarchy(design["source_index"]["hierarchy"])


def source_excerpt(
    project: ProjectConfig,
    file: str,
    line: int | None,
    *,
    radius: int = 10,
) -> dict[str, Any]:
    if radius < 0:
        raise ValueError("radius must be >= 0")

    source_paths = {path.resolve() for path in project.source_files()}
    candidate = Path(file)
    if not candidate.is_absolute():
        candidate = project.root / candidate
    candidate = candidate.resolve()
    if candidate not in source_paths:
        raise ValueError("source file is not part of the configured project")

    lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return {
            "file": str(candidate),
            "line": None,
            "start_line": 0,
            "end_line": 0,
            "text": "",
        }

    target = int(line or 1)
    target = min(max(target, 1), len(lines))
    start = max(1, target - radius)
    end = min(len(lines), target + radius)
    rendered = "\n".join(
        f"{number:>6}  {lines[number - 1]}"
        for number in range(start, end + 1)
    )
    return {
        "file": str(candidate),
        "line": target,
        "start_line": start,
        "end_line": end,
        "text": rendered,
    }


def build_debug_gui_snapshot(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Build the display-only data model used by the desktop Debug Studio."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    runs = list_run_records(project, limit=limit)
    formal_rows = list_formal_result_snapshots(project, limit=1)
    uvm_rows = list_uvm_log_snapshots(project, limit=1)
    source_index = build_design_index(project)
    elaborated = _load_elaborated_index(project)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": project.name,
        "project_root": str(project.root),
        "top": project.top,
        "simulator": project.simulator,
        "policy": {
            "display_only": True,
            "executes_verification": False,
            "invokes_ai": False,
            "applies_generated_artifacts": False,
            "writes_project_files": False,
        },
        "stats": run_statistics(project),
        "assertions": assertion_statistics(project),
        "runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": formal_rows[0] if formal_rows else None,
        "latest_uvm": uvm_rows[0] if uvm_rows else None,
        "design": {
            "source_index": source_index,
            "elaborated": elaborated,
        },
    }


def launch_debug_gui(project: ProjectConfig, *, limit: int = 100) -> int:
    """Launch the display-only Debug Studio using Python's Tk/ttk toolkit."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Tkinter is unavailable in this Python installation; install Python with Tk support."
        ) from exc

    try:
        root = tk.Tk()
    except Exception as exc:  # pragma: no cover - display server/platform dependent
        raise RuntimeError(f"Desktop GUI could not start: {exc}") from exc

    root.title(f"ZDDV Debug Studio — {project.name}")
    root.geometry("1280x820")
    root.minsize(960, 620)

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)

    header = ttk.Frame(outer)
    header.pack(fill="x")
    status_var = tk.StringVar(value="")
    ttk.Label(
        header,
        text=f"ZDDV Debug Studio · {project.name}",
        font=("TkDefaultFont", 16, "bold"),
    ).pack(side="left")
    ttk.Button(header, text="Refresh", command=lambda: refresh()).pack(side="right")
    ttk.Label(outer, textvariable=status_var).pack(fill="x", pady=(4, 10))

    summary = ttk.LabelFrame(outer, text="Verification Summary", padding=10)
    summary.pack(fill="x", pady=(0, 10))
    metric_vars = {
        "total": tk.StringVar(value="0"),
        "passed": tk.StringVar(value="0"),
        "failed": tk.StringVar(value="0"),
        "timed_out": tk.StringVar(value="0"),
        "pass_rate": tk.StringVar(value="0.0%"),
        "coverage": tk.StringVar(value="—"),
    }
    for column, (label, key) in enumerate(
        (
            ("Runs", "total"),
            ("Passed", "passed"),
            ("Failed", "failed"),
            ("Timeouts", "timed_out"),
            ("Pass rate", "pass_rate"),
            ("Coverage", "coverage"),
        )
    ):
        card = ttk.Frame(summary, padding=(8, 4))
        card.grid(row=0, column=column, sticky="nsew")
        ttk.Label(card, text=label).pack()
        ttk.Label(
            card,
            textvariable=metric_vars[key],
            font=("TkDefaultFont", 14, "bold"),
        ).pack()
        summary.columnconfigure(column, weight=1)

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)

    runs_tab = ttk.Frame(notebook, padding=8)
    failures_tab = ttk.Frame(notebook, padding=8)
    coverage_tab = ttk.Frame(notebook, padding=8)
    tests_tab = ttk.Frame(notebook, padding=8)
    evidence_tab = ttk.Frame(notebook, padding=8)
    source_tab = ttk.Frame(notebook, padding=8)
    hierarchy_tab = ttk.Frame(notebook, padding=8)
    for frame, label in (
        (runs_tab, "Runs"),
        (failures_tab, "Failures"),
        (coverage_tab, "Coverage"),
        (tests_tab, "Per-test"),
        (evidence_tab, "Evidence"),
        (source_tab, "Source"),
        (hierarchy_tab, "Hierarchy"),
    ):
        notebook.add(frame, text=label)

    def make_tree(parent, columns: tuple[str, ...], headings: tuple[str, ...]):
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for name, heading in zip(columns, headings):
            tree.heading(name, text=heading)
            tree.column(name, width=130, anchor="w")
        return tree

    def mount_tree(parent, tree) -> None:
        ybar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

    runs_tree = make_tree(
        runs_tab,
        ("status", "test", "seed", "simulator", "duration", "run_id"),
        ("Status", "Test", "Seed", "Simulator", "Time (ms)", "Run ID"),
    )
    failures_tree = make_tree(
        failures_tab,
        ("count", "statuses", "tests", "signature"),
        ("Count", "Status", "Tests", "Failure Signature"),
    )
    coverage_tree = make_tree(
        coverage_tab,
        ("metric", "percent", "covered", "total"),
        ("Metric", "Percent", "Covered", "Total"),
    )
    tests_tree = make_tree(
        tests_tab,
        ("test", "total", "passed", "failed", "timeout", "rate"),
        ("Test", "Total", "Pass", "Fail", "Timeout", "Pass rate"),
    )
    evidence_tree = make_tree(
        evidence_tab,
        ("kind", "status", "details", "identifier"),
        ("Evidence", "Status", "Details", "Snapshot / source"),
    )
    for tab, tree in (
        (runs_tab, runs_tree),
        (failures_tab, failures_tree),
        (coverage_tab, coverage_tree),
        (tests_tab, tests_tree),
        (evidence_tab, evidence_tree),
    ):
        mount_tree(tab, tree)

    def make_navigation_tab(parent, columns, headings):
        parent.rowconfigure(0, weight=3)
        parent.rowconfigure(1, weight=2)
        parent.columnconfigure(0, weight=1)
        table_frame = ttk.Frame(parent)
        table_frame.grid(row=0, column=0, sticky="nsew")
        table = make_tree(table_frame, columns, headings)
        mount_tree(table_frame, table)
        preview = tk.Text(parent, wrap="none", height=12)
        preview.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        preview.configure(state="disabled")
        return table, preview

    source_tree, source_preview = make_navigation_tab(
        source_tab,
        ("kind", "name", "file", "line", "end_line"),
        ("Kind", "Unit", "File", "Line", "End"),
    )
    hierarchy_tree, hierarchy_preview = make_navigation_tab(
        hierarchy_tab,
        ("path", "module", "file", "line", "source"),
        ("Path", "Module", "File", "Line", "Source"),
    )

    source_locations: dict[str, tuple[str, int | None]] = {}
    hierarchy_locations: dict[str, tuple[str, int | None]] = {}

    def clear(tree) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)

    def set_preview(widget, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def show_selected(tree, locations, preview) -> None:
        selected = tree.selection()
        if not selected:
            return
        location = locations.get(selected[0])
        if location is None:
            set_preview(preview, "No source location is available for this row.")
            return
        file, line = location
        try:
            excerpt = source_excerpt(project, file, line)
        except (OSError, ValueError) as exc:
            set_preview(preview, f"Source preview unavailable: {exc}")
            return
        set_preview(
            preview,
            f"{excerpt['file']} · line {excerpt['line']}\n\n{excerpt['text']}",
        )

    source_tree.bind(
        "<<TreeviewSelect>>",
        lambda _event: show_selected(source_tree, source_locations, source_preview),
    )
    hierarchy_tree.bind(
        "<<TreeviewSelect>>",
        lambda _event: show_selected(
            hierarchy_tree, hierarchy_locations, hierarchy_preview
        ),
    )

    def refresh() -> None:
        snapshot = build_debug_gui_snapshot(project, limit=limit)
        stats = snapshot["stats"]
        metric_vars["total"].set(str(stats["total"]))
        metric_vars["passed"].set(str(stats["passed"]))
        metric_vars["failed"].set(str(stats["failed"]))
        metric_vars["timed_out"].set(str(stats["timed_out"]))
        metric_vars["pass_rate"].set(f"{stats['pass_rate']:.1f}%")

        latest_coverage = snapshot["latest_coverage"]
        metric_vars["coverage"].set(
            "—"
            if latest_coverage is None
            else f"{latest_coverage['percent']:.1f}%"
        )

        clear(runs_tree)
        for row in snapshot["runs"]:
            duration = "—" if row["duration_ms"] is None else f"{row['duration_ms']:.1f}"
            runs_tree.insert(
                "",
                "end",
                values=(
                    row["status"],
                    row["test_name"] or "(default)",
                    "—" if row["seed"] is None else row["seed"],
                    row["simulator"],
                    duration,
                    row["run_id"],
                ),
            )

        clear(failures_tree)
        for group in snapshot["failure_groups"]:
            failures_tree.insert(
                "",
                "end",
                values=(
                    group["count"],
                    ", ".join(group["statuses"]) or "—",
                    ", ".join(group["tests"]) or "—",
                    group["signature"],
                ),
            )

        clear(coverage_tree)
        if latest_coverage is not None:
            for item in latest_coverage["items"]:
                percent = (
                    "—"
                    if item["percent"] is None
                    else f"{float(item['percent']):.1f}%"
                )
                coverage_tree.insert(
                    "",
                    "end",
                    values=(
                        item["metric"],
                        percent,
                        "—" if item["covered"] is None else item["covered"],
                        "—" if item["total"] is None else item["total"],
                    ),
                )

        clear(tests_tree)
        for row in stats["tests"]:
            tests_tree.insert(
                "",
                "end",
                values=(
                    row["test_name"],
                    row["total"],
                    row["passed"],
                    row["failed"],
                    row["timed_out"],
                    f"{row['pass_rate']:.1f}%",
                ),
            )

        clear(evidence_tree)
        assertions = snapshot["assertions"]
        assertion_status = (
            "FAIL"
            if assertions["failed"]
            else ("PASS" if assertions["total"] else "NOT_PRESENT")
        )
        evidence_tree.insert(
            "",
            "end",
            values=(
                "Assertions",
                assertion_status,
                f"{assertions['passed']}/{assertions['total']} passed; "
                f"{assertions['failed']} failed",
                "normalized assertion database",
            ),
        )
        if latest_coverage is None:
            evidence_tree.insert("", "end", values=("Coverage", "NOT_PRESENT", "—", "—"))
        else:
            evidence_tree.insert(
                "",
                "end",
                values=(
                    "Coverage",
                    "PRESENT",
                    f"{latest_coverage['percent']:.1f}% · "
                    f"{latest_coverage['kind']} · {latest_coverage['simulator']}",
                    latest_coverage["snapshot_id"],
                ),
            )

        formal = snapshot["latest_formal"]
        if formal is None:
            evidence_tree.insert("", "end", values=("Formal", "NOT_PRESENT", "—", "—"))
        else:
            evidence_tree.insert(
                "",
                "end",
                values=(
                    "Formal",
                    formal["status"],
                    f"{formal['mode']} · {formal['property_count']} properties",
                    formal["snapshot_id"],
                ),
            )

        uvm = snapshot["latest_uvm"]
        if uvm is None:
            evidence_tree.insert("", "end", values=("UVM", "NOT_PRESENT", "—", "—"))
        else:
            evidence_tree.insert(
                "",
                "end",
                values=(
                    "UVM",
                    uvm["status"],
                    f"test={uvm['test_name'] or '(unknown)'} · "
                    f"errors={uvm['error_count']} · fatals={uvm['fatal_count']}",
                    uvm["snapshot_id"],
                ),
            )

        elaborated = snapshot["design"]["elaborated"]
        evidence_tree.insert(
            "",
            "end",
            values=(
                "Elaborated hierarchy",
                elaborated["status"],
                (
                    f"{len(elaborated['instances'])} instances"
                    if elaborated["status"] == "PRESENT"
                    else elaborated.get("error", "No persisted elaborated index")
                ),
                elaborated["path"],
            ),
        )

        clear(source_tree)
        source_locations.clear()
        for unit in snapshot["design"]["source_index"]["units"]:
            item_id = source_tree.insert(
                "",
                "end",
                values=(
                    unit["kind"],
                    unit["name"],
                    unit["file"],
                    unit["line"],
                    unit["end_line"],
                ),
            )
            source_locations[item_id] = (unit["file"], unit["line"])

        clear(hierarchy_tree)
        hierarchy_locations.clear()
        for row in hierarchy_rows(snapshot):
            item_id = hierarchy_tree.insert(
                "",
                "end",
                values=(
                    row["path"] or "—",
                    row["module"] or "—",
                    row["file"] or "—",
                    "—" if row["line"] is None else row["line"],
                    row["source"],
                ),
            )
            if row["file"]:
                hierarchy_locations[item_id] = (row["file"], row["line"])

        status_var.set(
            f"Display-only · {snapshot['project_root']} · "
            f"{snapshot['simulator']} · top={snapshot['top']} · "
            f"refreshed {snapshot['generated_at']}"
        )

    refresh()
    root.mainloop()
    return 0

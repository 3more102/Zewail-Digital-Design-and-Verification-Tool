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


def _load_persisted_elaborated_index(project: ProjectConfig) -> dict[str, Any] | None:
    """Load an already-generated elaborated index without invoking a simulator."""
    path = project.root / ".zddv" / "design" / "elaborated.json"
    if not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if payload.get("project") != project.name or payload.get("top") != project.top:
        return None

    instances = payload.get("instances")
    modules = payload.get("modules")
    if not isinstance(instances, list) or not isinstance(modules, list):
        return None

    return {
        "path": str(path),
        "created_at": payload.get("created_at"),
        "simulator": payload.get("simulator"),
        "simulator_version": payload.get("simulator_version"),
        "source_format": payload.get("source_format"),
        "modules": modules,
        "instances": instances,
        "summary": payload.get(
            "summary",
            {"modules": len(modules), "instances": len(instances)},
        ),
    }


def build_desktop_snapshot(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Compose display-only Debug Studio state from project and persisted evidence."""
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
        },
        "stats": run_statistics(project),
        "assertions": assertion_statistics(project),
        "recent_runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": formal_rows[0] if formal_rows else None,
        "latest_uvm": uvm_rows[0] if uvm_rows else None,
        "design": design,
        "persisted_elaboration": _load_persisted_elaborated_index(project),
    }


def _source_path(project: ProjectConfig, indexed_path: str) -> Path:
    path = Path(indexed_path)
    if not path.is_absolute():
        path = project.root / path
    resolved = path.resolve()
    allowed = {source.resolve() for source in project.source_files()}
    if resolved not in allowed:
        raise RuntimeError(f"Source path is not part of the configured project: {indexed_path}")
    return resolved


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
    window.geometry("1280x820")
    window.minsize(960, 640)

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

    def make_tree(parent, columns, headings):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for column, title, width in headings:
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w")
        return tree

    run_tree = make_tree(
        run_tab,
        ("status", "test", "seed", "duration", "run_id"),
        (
            ("status", "Status", 90),
            ("test", "Test", 220),
            ("seed", "Seed", 90),
            ("duration", "Time (ms)", 110),
            ("run_id", "Run ID", 420),
        ),
    )
    failure_tree = make_tree(
        failure_tab,
        ("count", "status", "tests", "signature"),
        (
            ("count", "Count", 80),
            ("status", "Status", 120),
            ("tests", "Tests", 260),
            ("signature", "Normalized signature", 520),
        ),
    )
    evidence_tree = make_tree(
        evidence_tab,
        ("kind", "status", "details", "id"),
        (
            ("kind", "Evidence", 150),
            ("status", "Status", 130),
            ("details", "Details", 500),
            ("id", "Snapshot / source", 300),
        ),
    )

    source_pane = ttk.Panedwindow(source_tab, orient="horizontal")
    source_pane.pack(fill="both", expand=True)
    source_left = ttk.Frame(source_pane)
    source_right = ttk.Frame(source_pane)
    source_pane.add(source_left, weight=1)
    source_pane.add(source_right, weight=3)

    source_tree = ttk.Treeview(
        source_left,
        columns=("lines", "bytes", "sha256"),
        show="tree headings",
    )
    source_tree.heading("#0", text="Source file")
    source_tree.heading("lines", text="Lines")
    source_tree.heading("bytes", text="Bytes")
    source_tree.heading("sha256", text="SHA-256")
    source_tree.column("#0", width=280, anchor="w")
    source_tree.column("lines", width=80, anchor="e")
    source_tree.column("bytes", width=90, anchor="e")
    source_tree.column("sha256", width=260, anchor="w")
    source_tree.pack(fill="both", expand=True)

    source_title = tk.StringVar(value="Select a source file or hierarchy node")
    ttk.Label(source_right, textvariable=source_title).pack(fill="x")
    source_text = tk.Text(
        source_right,
        wrap="none",
        font=("TkFixedFont", 10),
        state="disabled",
    )
    source_y = ttk.Scrollbar(source_right, orient="vertical", command=source_text.yview)
    source_x = ttk.Scrollbar(source_right, orient="horizontal", command=source_text.xview)
    source_text.configure(yscrollcommand=source_y.set, xscrollcommand=source_x.set)
    source_text.pack(side="left", fill="both", expand=True)
    source_y.pack(side="right", fill="y")
    source_x.pack(side="bottom", fill="x")

    hierarchy_pane = ttk.Panedwindow(hierarchy_tab, orient="horizontal")
    hierarchy_pane.pack(fill="both", expand=True)
    hierarchy_left = ttk.Frame(hierarchy_pane)
    hierarchy_right = ttk.Frame(hierarchy_pane)
    hierarchy_pane.add(hierarchy_left, weight=2)
    hierarchy_pane.add(hierarchy_right, weight=3)

    hierarchy_tree = ttk.Treeview(
        hierarchy_left,
        columns=("type", "file", "line"),
        show="tree headings",
    )
    hierarchy_tree.heading("#0", text="Instance")
    hierarchy_tree.heading("type", text="Type")
    hierarchy_tree.heading("file", text="Source")
    hierarchy_tree.heading("line", text="Line")
    hierarchy_tree.column("#0", width=260, anchor="w")
    hierarchy_tree.column("type", width=180, anchor="w")
    hierarchy_tree.column("file", width=260, anchor="w")
    hierarchy_tree.column("line", width=70, anchor="e")
    hierarchy_tree.pack(fill="both", expand=True)

    hierarchy_info = tk.StringVar(value="")
    ttk.Label(hierarchy_right, textvariable=hierarchy_info).pack(fill="x")
    hierarchy_source = tk.Text(
        hierarchy_right,
        wrap="none",
        font=("TkFixedFont", 10),
        state="disabled",
    )
    hierarchy_source.pack(fill="both", expand=True)

    footer = ttk.Frame(container, padding=(0, 10, 0, 0))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        text=(
            "Display-only viewer: refresh reads project sources and persisted evidence; "
            "it does not run verification, invoke AI, or apply generated artifacts."
        ),
    ).pack(side="left")

    current: dict[str, Any] = {}
    source_items: dict[str, str] = {}
    hierarchy_items: dict[str, tuple[str | None, int | None]] = {}

    def _clear(tree) -> None:
        items = tree.get_children()
        if items:
            tree.delete(*items)

    def _render_source(widget, indexed_path: str, line: int | None = None) -> None:
        try:
            path = _source_path(project, indexed_path)
            source = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, RuntimeError) as exc:
            rendered = f"Unable to read source: {exc}\n"
        else:
            rendered = "".join(
                f"{number:6d}  {content}"
                for number, content in enumerate(source.splitlines(keepends=True), 1)
            )
            if source and not source.endswith("\n"):
                rendered += "\n"

        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", rendered)
        if line is not None and line > 0:
            widget.see(f"{line}.0")
            widget.tag_remove("focus_line", "1.0", "end")
            widget.tag_add("focus_line", f"{line}.0", f"{line}.end")
        widget.configure(state="disabled")

    def _on_source_select(_event=None) -> None:
        selected = source_tree.selection()
        if not selected:
            return
        path = source_items.get(selected[0])
        if not path:
            return
        source_title.set(path)
        _render_source(source_text, path)

    def _on_hierarchy_select(_event=None) -> None:
        selected = hierarchy_tree.selection()
        if not selected:
            return
        location = hierarchy_items.get(selected[0])
        if not location:
            hierarchy_info.set("No resolved source location for this hierarchy node.")
            return
        path, line = location
        if not path:
            hierarchy_info.set("No resolved source location for this hierarchy node.")
            return
        hierarchy_info.set(f"{path}" + (f":{line}" if line else ""))
        _render_source(hierarchy_source, path, line)

    source_tree.bind("<<TreeviewSelect>>", _on_source_select)
    hierarchy_tree.bind("<<TreeviewSelect>>", _on_hierarchy_select)

    def _insert_source_hierarchy(parent: str, node: dict[str, Any]) -> None:
        file = node.get("file")
        line = node.get("line")
        label = str(node.get("instance") or node.get("path") or node.get("type") or "?")
        item = hierarchy_tree.insert(
            parent,
            "end",
            text=label,
            values=(
                node.get("type") or "?",
                file or "-",
                "-" if line is None else line,
            ),
            open=parent == "",
        )
        hierarchy_items[item] = (str(file) if file else None, int(line) if line else None)
        for child in node.get("children", []):
            _insert_source_hierarchy(item, child)

    def _insert_persisted_elaboration(parent: str, data: dict[str, Any]) -> None:
        by_path = {
            str(row.get("path")): row
            for row in data.get("instances", [])
            if row.get("path")
        }
        children: dict[str, list[str]] = {}
        for path in by_path:
            parent_path = path.rsplit(".", 1)[0] if "." in path else ""
            children.setdefault(parent_path, []).append(path)
        for paths in children.values():
            paths.sort()

        def visit(path: str, parent_item: str) -> None:
            row = by_path[path]
            location = row.get("location") or {}
            file = location.get("path")
            line = location.get("line")
            item = hierarchy_tree.insert(
                parent_item,
                "end",
                text=row.get("name") or path.rsplit(".", 1)[-1],
                values=(
                    row.get("module") or "?",
                    file or "-",
                    "-" if line is None else line,
                ),
                open=parent_item == "",
            )
            hierarchy_items[item] = (
                str(file) if file else None,
                int(line) if line else None,
            )
            for child_path in children.get(path, []):
                visit(child_path, item)

        roots = children.get("", [])
        if not roots and project.top in by_path:
            roots = [project.top]
        for path in roots:
            visit(path, parent)

    def refresh() -> None:
        nonlocal current
        current = build_desktop_snapshot(project, limit=limit)
        stats = current["stats"]
        coverage = current["latest_coverage"]
        design = current["design"]

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

        _clear(source_tree)
        source_items.clear()
        units_by_file: dict[str, list[dict[str, Any]]] = {}
        for unit in design["units"]:
            units_by_file.setdefault(str(unit["file"]), []).append(unit)
        for row in design["files"]:
            path = str(row["path"])
            item = source_tree.insert(
                "",
                "end",
                text=path,
                values=(row["lines"], row["bytes"], row["sha256"]),
            )
            source_items[item] = path
            for unit in units_by_file.get(path, []):
                child = source_tree.insert(
                    item,
                    "end",
                    text=f"{unit['kind']} {unit['name']} @ line {unit['line']}",
                    values=("", "", ""),
                )
                source_items[child] = path

        _clear(hierarchy_tree)
        hierarchy_items.clear()
        elaborated = current["persisted_elaboration"]
        if elaborated is not None and elaborated.get("instances"):
            root = hierarchy_tree.insert(
                "",
                "end",
                text="Elaborated hierarchy (persisted)",
                values=(
                    elaborated.get("simulator") or project.simulator,
                    elaborated.get("path") or "-",
                    "-",
                ),
                open=True,
            )
            _insert_persisted_elaboration(root, elaborated)
        else:
            root = hierarchy_tree.insert(
                "",
                "end",
                text="Source-level hierarchy",
                values=("deterministic source index", "-", "-"),
                open=True,
            )
            _insert_source_hierarchy(root, design["hierarchy"])

        status_text.set(
            f"{project.simulator} · top={project.top} · "
            f"{design['summary']['files']} sources · "
            f"showing {len(current['recent_runs'])} runs"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

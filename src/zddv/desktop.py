from __future__ import annotations

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
    notebook.add(design_tab, text="Source + Hierarchy")

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


    design_pane = ttk.PanedWindow(design_tab, orient="horizontal")
    design_pane.pack(fill="both", expand=True)

    files_frame = ttk.LabelFrame(design_pane, text="Sources", padding=6)
    hierarchy_frame = ttk.LabelFrame(design_pane, text="Hierarchy", padding=6)
    source_frame = ttk.LabelFrame(design_pane, text="Read-only source", padding=6)
    design_pane.add(files_frame, weight=2)
    design_pane.add(hierarchy_frame, weight=3)
    design_pane.add(source_frame, weight=5)

    source_columns = ("path", "lines")
    source_tree = ttk.Treeview(
        files_frame,
        columns=source_columns,
        show="headings",
        selectmode="browse",
    )
    source_tree.heading("path", text="Source file")
    source_tree.heading("lines", text="Lines")
    source_tree.column("path", width=260, anchor="w")
    source_tree.column("lines", width=70, anchor="e")
    source_tree.pack(fill="both", expand=True)

    hierarchy_tree = ttk.Treeview(
        hierarchy_frame,
        columns=("type", "file", "line"),
        show="tree headings",
        selectmode="browse",
    )
    hierarchy_tree.heading("#0", text="Instance")
    hierarchy_tree.heading("type", text="Type")
    hierarchy_tree.heading("file", text="Source")
    hierarchy_tree.heading("line", text="Line")
    hierarchy_tree.column("#0", width=220, anchor="w")
    hierarchy_tree.column("type", width=150, anchor="w")
    hierarchy_tree.column("file", width=240, anchor="w")
    hierarchy_tree.column("line", width=60, anchor="e")
    hierarchy_tree.pack(fill="both", expand=True)

    source_status = tk.StringVar(value="Select a source file or hierarchy node.")
    ttk.Label(source_frame, textvariable=source_status).pack(fill="x", pady=(0, 6))

    source_text_frame = ttk.Frame(source_frame)
    source_text_frame.pack(fill="both", expand=True)
    source_text = tk.Text(
        source_text_frame,
        wrap="none",
        state="disabled",
        font="TkFixedFont",
    )
    source_y = ttk.Scrollbar(source_text_frame, orient="vertical", command=source_text.yview)
    source_x = ttk.Scrollbar(source_text_frame, orient="horizontal", command=source_text.xview)
    source_text.configure(yscrollcommand=source_y.set, xscrollcommand=source_x.set)
    source_text.grid(row=0, column=0, sticky="nsew")
    source_y.grid(row=0, column=1, sticky="ns")
    source_x.grid(row=1, column=0, sticky="ew")
    source_text_frame.rowconfigure(0, weight=1)
    source_text_frame.columnconfigure(0, weight=1)

    footer = ttk.Frame(container, padding=(0, 10, 0, 0))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        text=(
            "Display-only viewer: refresh reads persisted evidence and project sources; "
            "it does not run verification, invoke AI, or apply generated artifacts."
        ),
    ).pack(side="left")

    current: dict[str, Any] = {}

    def _clear(tree) -> None:
        for item in tree.get_children():
            tree.delete(item)


    hierarchy_items: dict[str, dict[str, Any]] = {}

    def _show_source(relative_path: str | None, line: int | None = None) -> None:
        source_text.configure(state="normal")
        source_text.delete("1.0", "end")
        if not relative_path:
            source_text.insert("1.0", "No source location is available for this item.\n")
            source_text.configure(state="disabled")
            source_status.set("No source location is available for this item.")
            return

        root = project.root.resolve()
        candidate = (project.root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            source_text.insert("1.0", "Blocked source path outside the project root.\n")
            source_text.configure(state="disabled")
            source_status.set(f"Blocked source path: {relative_path}")
            return

        if not candidate.is_file():
            source_text.insert("1.0", f"Source file is not available: {relative_path}\n")
            source_text.configure(state="disabled")
            source_status.set(f"Missing source: {relative_path}")
            return

        source_text.insert(
            "1.0",
            candidate.read_text(encoding="utf-8", errors="replace"),
        )
        if isinstance(line, int) and line > 0:
            source_text.tag_remove("selected_line", "1.0", "end")
            source_text.tag_add("selected_line", f"{line}.0", f"{line}.end")
            source_text.see(f"{line}.0")
            source_status.set(f"{relative_path} · line {line}")
        else:
            source_text.see("1.0")
            source_status.set(relative_path)
        source_text.configure(state="disabled")

    def _insert_hierarchy(node: dict[str, Any], parent: str = "") -> None:
        resolved = bool(node.get("resolved", True))
        recursive = bool(node.get("recursive", False))
        suffix = " [UNRESOLVED]" if not resolved else (" [RECURSIVE]" if recursive else "")
        item_id = hierarchy_tree.insert(
            parent,
            "end",
            text=f"{node.get('instance', '?')}{suffix}",
            values=(
                node.get("type", "?"),
                node.get("file", "-"),
                node.get("line", "-"),
            ),
            open=True,
        )
        hierarchy_items[item_id] = node
        for child in node.get("children", []):
            _insert_hierarchy(child, item_id)

    def _on_source_selected(_event=None) -> None:
        selected = source_tree.selection()
        if not selected:
            return
        values = source_tree.item(selected[0], "values")
        if values:
            _show_source(str(values[0]))

    def _on_hierarchy_selected(_event=None) -> None:
        selected = hierarchy_tree.selection()
        if not selected:
            return
        node = hierarchy_items.get(selected[0], {})
        raw_line = node.get("line")
        line = int(raw_line) if isinstance(raw_line, int) else None
        _show_source(node.get("file"), line)

    source_tree.bind("<<TreeviewSelect>>", _on_source_selected)
    hierarchy_tree.bind("<<TreeviewSelect>>", _on_hierarchy_selected)

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
        _clear(source_tree)
        for source in design["files"]:
            source_tree.insert(
                "",
                "end",
                values=(source["path"], source["lines"]),
            )

        _clear(hierarchy_tree)
        hierarchy_items.clear()
        _insert_hierarchy(design["hierarchy"])

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
            f"{current['design']['summary']['files']} source files · "
            f"{current['design']['summary']['units']} units · "
            f"showing {len(current['recent_runs'])} runs"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

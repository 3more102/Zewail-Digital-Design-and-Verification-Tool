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


def _flatten_source_hierarchy(node: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(item: dict[str, Any], depth: int) -> None:
        rows.append(
            {
                "depth": depth,
                "instance": item.get("instance"),
                "type": item.get("type"),
                "path": item.get("path"),
                "resolved": bool(item.get("resolved", True)),
                "recursive": bool(item.get("recursive", False)),
                "file": item.get("file"),
                "line": item.get("line"),
            }
        )
        for child in item.get("children", []):
            visit(child, depth + 1)

    visit(node, 0)
    return rows


def _load_persisted_elaboration(project: ProjectConfig) -> dict[str, Any]:
    path = (project.root / ".zddv" / "design" / "elaborated.json").resolve()
    if not path.exists():
        return {"state": "NOT_PRESENT", "path": str(path), "index": None}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "state": "INVALID",
            "path": str(path),
            "index": None,
            "error": str(exc),
        }

    if not isinstance(payload, dict) or not isinstance(payload.get("instances", []), list):
        return {
            "state": "INVALID",
            "path": str(path),
            "index": None,
            "error": "elaborated.json does not match the normalized hierarchy shape",
        }

    return {"state": "PRESENT", "path": str(path), "index": payload}


def _source_path(project: ProjectConfig, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    return path.resolve()


def _read_source(project: ProjectConfig, value: str) -> str:
    return _source_path(project, value).read_text(
        encoding="utf-8",
        errors="replace",
    )


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
        },
        "stats": run_statistics(project),
        "assertions": assertion_statistics(project),
        "recent_runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": formal_rows[0] if formal_rows else None,
        "latest_uvm": uvm_rows[0] if uvm_rows else None,
        "source_index": source_index,
        "source_hierarchy": _flatten_source_hierarchy(source_index["hierarchy"]),
        "elaborated_hierarchy": _load_persisted_elaboration(project),
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
    source_pane.add(source_left, weight=2)
    source_pane.add(source_right, weight=5)

    source_tree = ttk.Treeview(
        source_left,
        columns=("kind", "line"),
        show="tree headings",
    )
    source_tree.heading("#0", text="Source / Unit")
    source_tree.heading("kind", text="Kind")
    source_tree.heading("line", text="Line")
    source_tree.column("#0", width=320, anchor="w")
    source_tree.column("kind", width=100, anchor="w")
    source_tree.column("line", width=80, anchor="w")
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
    source_ybar = ttk.Scrollbar(source_right, orient="vertical", command=source_text.yview)
    source_xbar = ttk.Scrollbar(source_right, orient="horizontal", command=source_text.xview)
    source_text.configure(yscrollcommand=source_ybar.set, xscrollcommand=source_xbar.set)
    source_text.grid(row=1, column=0, sticky="nsew")
    source_ybar.grid(row=1, column=1, sticky="ns")
    source_xbar.grid(row=2, column=0, sticky="ew")
    source_right.rowconfigure(1, weight=1)
    source_right.columnconfigure(0, weight=1)
    source_text.configure(state="disabled")

    hierarchy_book = ttk.Notebook(hierarchy_tab)
    hierarchy_book.pack(fill="both", expand=True)
    source_hierarchy_tab = ttk.Frame(hierarchy_book, padding=4)
    elaborated_hierarchy_tab = ttk.Frame(hierarchy_book, padding=4)
    hierarchy_book.add(source_hierarchy_tab, text="Source-level")
    hierarchy_book.add(elaborated_hierarchy_tab, text="Persisted elaborated")

    source_hierarchy_tree = ttk.Treeview(
        source_hierarchy_tab,
        columns=("type", "file", "line", "state", "path"),
        show="tree headings",
    )
    for column, title, width in (
        ("#0", "Instance", 220),
        ("type", "Type", 180),
        ("file", "Source", 280),
        ("line", "Line", 70),
        ("state", "State", 110),
        ("path", "Hierarchy path", 360),
    ):
        source_hierarchy_tree.heading(column, text=title)
        source_hierarchy_tree.column(column, width=width, anchor="w")
    source_hierarchy_tree.pack(fill="both", expand=True)

    elaborated_status_text = tk.StringVar(value="")
    ttk.Label(
        elaborated_hierarchy_tab,
        textvariable=elaborated_status_text,
    ).pack(fill="x", pady=(0, 6))
    elaborated_tree = ttk.Treeview(
        elaborated_hierarchy_tab,
        columns=("module", "file", "line", "path"),
        show="tree headings",
    )
    for column, title, width in (
        ("#0", "Instance", 220),
        ("module", "Module", 180),
        ("file", "Source", 280),
        ("line", "Line", 70),
        ("path", "Hierarchy path", 420),
    ):
        elaborated_tree.heading(column, text=title)
        elaborated_tree.column(column, width=width, anchor="w")
    elaborated_tree.pack(fill="both", expand=True)

    footer = ttk.Frame(container, padding=(0, 10, 0, 0))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        text=(
            "Display-only viewer: refresh reads persisted verification evidence and "
            "current source files; it does not run verification, invoke AI, write "
            "design indexes, or apply generated artifacts."
        ),
    ).pack(side="left")

    current: dict[str, Any] = {}
    source_targets: dict[str, tuple[str, int | None]] = {}
    source_hierarchy_targets: dict[str, tuple[str, int | None]] = {}
    elaborated_targets: dict[str, tuple[str, int | None]] = {}

    def _clear(tree) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _show_source(path_value: str, line: int | None = None) -> None:
        try:
            text = _read_source(project, path_value)
        except OSError as exc:
            source_title_text.set(f"{path_value} · unavailable: {exc}")
            text = ""
            line = None

        source_text.configure(state="normal")
        source_text.delete("1.0", "end")
        source_text.insert("1.0", text)
        source_text.configure(state="disabled")
        if line and line > 0:
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
        notebook.select(source_tab)
        _show_source(*target)

    source_tree.bind("<<TreeviewSelect>>", _on_source_select)
    source_hierarchy_tree.bind(
        "<Double-1>",
        lambda _event: _open_hierarchy_source(
            source_hierarchy_tree,
            source_hierarchy_targets,
        ),
    )
    elaborated_tree.bind(
        "<Double-1>",
        lambda _event: _open_hierarchy_source(
            elaborated_tree,
            elaborated_targets,
        ),
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

        source_targets.clear()
        _clear(source_tree)
        units_by_file: dict[str, list[dict[str, Any]]] = {}
        for unit in current["source_index"]["units"]:
            units_by_file.setdefault(unit["file"], []).append(unit)

        for file_row in current["source_index"]["files"]:
            source_path = file_row["path"]
            file_id = source_tree.insert(
                "",
                "end",
                text=source_path,
                values=("file", 1),
                open=True,
            )
            source_targets[file_id] = (source_path, 1)
            for unit in units_by_file.get(source_path, []):
                unit_id = source_tree.insert(
                    file_id,
                    "end",
                    text=unit["name"],
                    values=(unit["kind"], unit["line"]),
                )
                source_targets[unit_id] = (source_path, int(unit["line"]))

        source_hierarchy_targets.clear()
        _clear(source_hierarchy_tree)
        source_ids: dict[str, str] = {}
        for row in current["source_hierarchy"]:
            hierarchy_path = str(row["path"] or "")
            parent_path = hierarchy_path.rsplit(".", 1)[0] if "." in hierarchy_path else ""
            parent_id = source_ids.get(parent_path, "")
            state = (
                "UNRESOLVED"
                if not row["resolved"]
                else ("RECURSIVE" if row["recursive"] else "RESOLVED")
            )
            item_id = source_hierarchy_tree.insert(
                parent_id,
                "end",
                text=row["instance"] or hierarchy_path,
                values=(
                    row["type"] or "-",
                    row["file"] or "-",
                    row["line"] or "-",
                    state,
                    hierarchy_path,
                ),
                open=row["depth"] < 2,
            )
            source_ids[hierarchy_path] = item_id
            if row["file"]:
                source_hierarchy_targets[item_id] = (
                    str(row["file"]),
                    int(row["line"]) if row["line"] else None,
                )

        elaborated_targets.clear()
        _clear(elaborated_tree)
        elaborated = current["elaborated_hierarchy"]
        if elaborated["state"] != "PRESENT":
            detail = elaborated.get("error", "")
            elaborated_status_text.set(
                f"{elaborated['state']} · {elaborated['path']}"
                + (f" · {detail}" if detail else "")
            )
        else:
            index = elaborated["index"]
            elaborated_status_text.set(
                f"PRESENT · {index.get('simulator', 'simulator')} · "
                f"{index.get('source_format', 'normalized')} · {elaborated['path']}"
            )
            elaborated_ids: dict[str, str] = {}
            for row in index.get("instances", []):
                hierarchy_path = str(row.get("path") or "")
                if not hierarchy_path:
                    continue
                parent_path = hierarchy_path.rsplit(".", 1)[0] if "." in hierarchy_path else ""
                parent_id = elaborated_ids.get(parent_path, "")
                location = row.get("location") or {}
                item_id = elaborated_tree.insert(
                    parent_id,
                    "end",
                    text=row.get("name") or hierarchy_path.rsplit(".", 1)[-1],
                    values=(
                        row.get("module") or "-",
                        location.get("path") or "-",
                        location.get("line") or "-",
                        hierarchy_path,
                    ),
                    open=hierarchy_path.count(".") < 2,
                )
                elaborated_ids[hierarchy_path] = item_id
                if location.get("path"):
                    elaborated_targets[item_id] = (
                        str(location["path"]),
                        int(location["line"]) if location.get("line") else None,
                    )

        status_text.set(
            f"{project.simulator} · top={project.top} · "
            f"showing {len(current['recent_runs'])} runs"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

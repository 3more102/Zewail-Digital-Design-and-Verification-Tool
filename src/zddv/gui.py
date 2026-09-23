from __future__ import annotations

from datetime import datetime, timezone
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
    """Return the newest normalized coverage snapshot across supported schemas."""
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
                "breakdown": dict(row.get("by_metric") or {}),
                "explicit_counts": dict(row.get("by_metric_counts") or {}),
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
                "breakdown": dict(row.get("by_type") or {}),
                "explicit_counts": {},
            }
        )

    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item["created_at"]))


def build_debug_gui_snapshot(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Compose the display-only Debug Studio model from authoritative ZDDV evidence."""
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
            "mutates_project_files": False,
        },
        "stats": run_statistics(project),
        "assertions": assertion_statistics(project),
        "runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": formal_rows[0] if formal_rows else None,
        "latest_uvm": uvm_rows[0] if uvm_rows else None,
        "design": design,
    }


def _coverage_rows(coverage: dict[str, Any] | None) -> list[tuple[str, str, str, str]]:
    if coverage is None:
        return []

    rows: list[tuple[str, str, str, str]] = []
    if coverage["kind"] == "points":
        for name, values in sorted(coverage["breakdown"].items()):
            rows.append(
                (
                    str(name),
                    str(values.get("total", "-")),
                    str(values.get("hit", "-")),
                    f"{float(values.get('hit_rate', 0.0)):.1f}%",
                )
            )
        return rows

    explicit_counts = coverage.get("explicit_counts") or {}
    for name, percent in sorted(coverage["breakdown"].items()):
        counts = explicit_counts.get(name) or {}
        rows.append(
            (
                str(name),
                str(counts.get("total", "-")),
                str(counts.get("covered", counts.get("hit", "-"))),
                f"{float(percent):.1f}%",
            )
        )
    return rows


def launch_debug_gui(project: ProjectConfig, *, limit: int = 100) -> int:
    """Launch the read-only Tk/ttk Debug Studio."""
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
        raise RuntimeError(f"Debug Studio could not start: {exc}") from exc

    root.title(f"ZDDV Debug Studio — {project.name}")
    root.geometry("1280x820")
    root.minsize(980, 620)

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)

    header = ttk.Frame(outer)
    header.pack(fill="x")
    ttk.Label(
        header,
        text=f"ZDDV Debug Studio · {project.name}",
        font=("TkDefaultFont", 16, "bold"),
    ).pack(side="left")
    status_var = tk.StringVar(value="")
    ttk.Button(header, text="Refresh", command=lambda: refresh()).pack(side="right")
    ttk.Label(outer, textvariable=status_var).pack(fill="x", pady=(4, 10))

    summary = ttk.LabelFrame(outer, text="Verification Summary", padding=10)
    summary.pack(fill="x", pady=(0, 10))
    metric_vars = {
        key: tk.StringVar(value="—")
        for key in ("total", "passed", "failed", "timed_out", "pass_rate", "coverage")
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

    def make_tab(name: str):
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text=name)
        return tab

    def make_tree(parent, columns: tuple[str, ...], headings: tuple[str, ...]):
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for name, heading in zip(columns, headings):
            tree.heading(name, text=heading)
            tree.column(name, width=135, anchor="w")
        ybar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        return tree

    runs_tree = make_tree(
        make_tab("Runs"),
        ("status", "test", "seed", "simulator", "duration", "run_id"),
        ("Status", "Test", "Seed", "Simulator", "Time (ms)", "Run ID"),
    )
    failures_tree = make_tree(
        make_tab("Failures"),
        ("count", "statuses", "tests", "signature"),
        ("Count", "Status", "Tests", "Failure Signature"),
    )
    coverage_tree = make_tree(
        make_tab("Coverage"),
        ("metric", "total", "covered", "rate"),
        ("Metric", "Total", "Covered", "Rate"),
    )
    tests_tree = make_tree(
        make_tab("Per-test"),
        ("test", "total", "passed", "failed", "timeout", "rate"),
        ("Test", "Total", "Pass", "Fail", "Timeout", "Pass rate"),
    )
    sources_tree = make_tree(
        make_tab("Sources"),
        ("kind", "name", "location", "details"),
        ("Kind", "Name", "Location", "Evidence"),
    )

    hierarchy_tab = make_tab("Hierarchy")
    hierarchy_tree = ttk.Treeview(
        hierarchy_tab,
        columns=("type", "file", "line", "state"),
        show="tree headings",
    )
    hierarchy_tree.heading("#0", text="Instance")
    hierarchy_tree.heading("type", text="Type")
    hierarchy_tree.heading("file", text="File")
    hierarchy_tree.heading("line", text="Line")
    hierarchy_tree.heading("state", text="State")
    hierarchy_tree.column("#0", width=260, anchor="w")
    hierarchy_tree.column("type", width=180, anchor="w")
    hierarchy_tree.column("file", width=380, anchor="w")
    hierarchy_tree.column("line", width=80, anchor="w")
    hierarchy_tree.column("state", width=120, anchor="w")
    hierarchy_ybar = ttk.Scrollbar(
        hierarchy_tab, orient="vertical", command=hierarchy_tree.yview
    )
    hierarchy_xbar = ttk.Scrollbar(
        hierarchy_tab, orient="horizontal", command=hierarchy_tree.xview
    )
    hierarchy_tree.configure(
        yscrollcommand=hierarchy_ybar.set,
        xscrollcommand=hierarchy_xbar.set,
    )
    hierarchy_tree.grid(row=0, column=0, sticky="nsew")
    hierarchy_ybar.grid(row=0, column=1, sticky="ns")
    hierarchy_xbar.grid(row=1, column=0, sticky="ew")
    hierarchy_tab.rowconfigure(0, weight=1)
    hierarchy_tab.columnconfigure(0, weight=1)

    evidence_tree = make_tree(
        make_tab("Evidence"),
        ("kind", "status", "details", "source"),
        ("Evidence", "Status", "Details", "Snapshot / Source"),
    )

    ttk.Label(
        outer,
        text=(
            "Read-only viewer: refresh reads project sources and persisted ZDDV evidence; "
            "it never runs verification, invokes AI, applies generated artifacts, or mutates files."
        ),
    ).pack(fill="x", pady=(10, 0))

    def clear(tree) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)

    def add_hierarchy(parent: str, node: dict[str, Any]) -> None:
        if not node.get("resolved", True):
            state = "UNRESOLVED"
        elif node.get("recursive"):
            state = "RECURSIVE"
        else:
            state = "RESOLVED"
        item = hierarchy_tree.insert(
            parent,
            "end",
            text=node.get("instance", "—"),
            values=(
                node.get("type", "—"),
                node.get("file", "—"),
                node.get("line", "—"),
                state,
            ),
            open=parent == "",
        )
        for child in node.get("children", []):
            add_hierarchy(item, child)

    def refresh() -> None:
        snapshot = build_debug_gui_snapshot(project, limit=limit)
        stats = snapshot["stats"]
        coverage = snapshot["latest_coverage"]

        metric_vars["total"].set(str(stats["total"]))
        metric_vars["passed"].set(str(stats["passed"]))
        metric_vars["failed"].set(str(stats["failed"]))
        metric_vars["timed_out"].set(str(stats["timed_out"]))
        metric_vars["pass_rate"].set(f"{stats['pass_rate']:.1f}%")
        metric_vars["coverage"].set(
            "—" if coverage is None else f"{coverage['percent']:.1f}%"
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
        for row in _coverage_rows(coverage):
            coverage_tree.insert("", "end", values=row)

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

        clear(sources_tree)
        for source in snapshot["design"]["files"]:
            sources_tree.insert(
                "",
                "end",
                values=(
                    "file",
                    source["path"],
                    "",
                    f"{source['lines']} lines · {source['bytes']} bytes · "
                    f"sha256={source['sha256']}",
                ),
            )
        for unit in snapshot["design"]["units"]:
            sources_tree.insert(
                "",
                "end",
                values=(
                    unit["kind"],
                    unit["name"],
                    f"{unit['file']}:{unit['line']}",
                    f"lines {unit['line']}-{unit['end_line']} · "
                    f"{len(unit['instances'])} child instances",
                ),
            )

        clear(hierarchy_tree)
        add_hierarchy("", snapshot["design"]["hierarchy"])

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

        if coverage is None:
            evidence_tree.insert("", "end", values=("Coverage", "NOT_PRESENT", "—", "—"))
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

        formal = snapshot["latest_formal"]
        if formal is None:
            evidence_tree.insert("", "end", values=("Formal", "NOT_PRESENT", "—", "—"))
        else:
            details = (
                f"{formal.get('mode', '—')} · "
                f"{formal.get('property_count', '—')} properties"
            )
            evidence_tree.insert(
                "",
                "end",
                values=(
                    "Formal",
                    formal.get("status", "PRESENT"),
                    details,
                    formal.get("snapshot_id", "—"),
                ),
            )

        uvm = snapshot["latest_uvm"]
        if uvm is None:
            evidence_tree.insert("", "end", values=("UVM", "NOT_PRESENT", "—", "—"))
        else:
            details = (
                f"test={uvm.get('test_name') or '(unknown)'} · "
                f"errors={uvm.get('error_count', '—')} · "
                f"fatals={uvm.get('fatal_count', '—')}"
            )
            evidence_tree.insert(
                "",
                "end",
                values=(
                    "UVM",
                    uvm.get("status", "PRESENT"),
                    details,
                    uvm.get("snapshot_id", "—"),
                ),
            )

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        design_summary = snapshot["design"]["summary"]
        status_var.set(
            f"Read-only · {snapshot['project']['simulator']} · "
            f"top={snapshot['project']['top']} · "
            f"{design_summary['units']} units · refreshed {now}"
        )

    refresh()
    root.mainloop()
    return 0

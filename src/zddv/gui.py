from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import list_coverage_snapshots, list_run_records, run_statistics
from zddv.triage import group_failure_records


def build_debug_gui_snapshot(project: ProjectConfig, *, limit: int = 100) -> dict[str, Any]:
    """Build the read-only data model used by the desktop Debug Studio."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    runs = list_run_records(project, limit=limit)
    coverage_rows = list_coverage_snapshots(project, limit=1)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": project.name,
        "project_root": str(project.root),
        "stats": run_statistics(project),
        "runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": coverage_rows[0] if coverage_rows else None,
    }


def launch_debug_gui(project: ProjectConfig, *, limit: int = 100) -> int:
    """Launch the localhost desktop Debug Studio using Python's Tk/ttk toolkit."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Tkinter is unavailable in this Python installation; install Python with Tk support."
        ) from exc

    root = tk.Tk()
    root.title(f"ZDDV Debug Studio — {project.name}")
    root.geometry("1180x760")
    root.minsize(900, 560)

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)

    header = ttk.Frame(outer)
    header.pack(fill="x")
    title_var = tk.StringVar(value=f"ZDDV Debug Studio · {project.name}")
    status_var = tk.StringVar(value="")
    ttk.Label(header, textvariable=title_var, font=("TkDefaultFont", 16, "bold")).pack(
        side="left"
    )
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
    metric_labels = (
        ("Runs", "total"),
        ("Passed", "passed"),
        ("Failed", "failed"),
        ("Timeouts", "timed_out"),
        ("Pass rate", "pass_rate"),
        ("Coverage", "coverage"),
    )
    for column, (label, key) in enumerate(metric_labels):
        card = ttk.Frame(summary, padding=(8, 4))
        card.grid(row=0, column=column, sticky="nsew")
        ttk.Label(card, text=label).pack()
        ttk.Label(card, textvariable=metric_vars[key], font=("TkDefaultFont", 14, "bold")).pack()
        summary.columnconfigure(column, weight=1)

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)

    runs_tab = ttk.Frame(notebook, padding=8)
    failures_tab = ttk.Frame(notebook, padding=8)
    coverage_tab = ttk.Frame(notebook, padding=8)
    tests_tab = ttk.Frame(notebook, padding=8)
    notebook.add(runs_tab, text="Runs")
    notebook.add(failures_tab, text="Failures")
    notebook.add(coverage_tab, text="Coverage")
    notebook.add(tests_tab, text="Per-test")

    def make_tree(parent, columns: tuple[str, ...], headings: tuple[str, ...]):
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for name, heading in zip(columns, headings):
            tree.heading(name, text=heading)
            tree.column(name, width=130, anchor="w")
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
        ("type", "total", "hit", "rate"),
        ("Type", "Total", "Hit", "Hit rate"),
    )
    tests_tree = make_tree(
        tests_tab,
        ("test", "total", "passed", "failed", "timeout", "rate"),
        ("Test", "Total", "Pass", "Fail", "Timeout", "Pass rate"),
    )

    def clear(tree) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)

    def refresh() -> None:
        snapshot = build_debug_gui_snapshot(project, limit=limit)
        stats = snapshot["stats"]
        metric_vars["total"].set(str(stats["total"]))
        metric_vars["passed"].set(str(stats["passed"]))
        metric_vars["failed"].set(str(stats["failed"]))
        metric_vars["timed_out"].set(str(stats["timed_out"]))
        metric_vars["pass_rate"].set(f"{stats['pass_rate']:.1f}%")

        latest_coverage = snapshot["latest_coverage"]
        if latest_coverage is None:
            metric_vars["coverage"].set("—")
        else:
            metric_vars["coverage"].set(f"{latest_coverage['hit_rate']:.1f}%")

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
            for kind, values in latest_coverage["by_type"].items():
                coverage_tree.insert(
                    "",
                    "end",
                    values=(
                        kind,
                        values["total"],
                        values["hit"],
                        f"{values['hit_rate']:.1f}%",
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

        status_var.set(
            f"Read-only view · {snapshot['project_root']} · refreshed {snapshot['generated_at']}"
        )

    refresh()
    root.mainloop()
    return 0

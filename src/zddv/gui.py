from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zddv.config import ProjectConfig
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


def build_debug_gui_snapshot(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Compose the read-only Debug Studio model from persisted ZDDV evidence."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    runs = list_run_records(project, limit=limit)
    formal_rows = list_formal_result_snapshots(project, limit=1)
    uvm_rows = list_uvm_log_snapshots(project, limit=1)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
        "runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": formal_rows[0] if formal_rows else None,
        "latest_uvm": uvm_rows[0] if uvm_rows else None,
    }


def launch_debug_gui(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> int:
    """Launch the read-only Tk/ttk ZDDV Debug Studio."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise RuntimeError(
            "Debug Studio requires Python with tkinter support installed."
        ) from exc

    try:
        root = tk.Tk()
    except Exception as exc:  # pragma: no cover - display server/platform dependent
        raise RuntimeError(f"Debug Studio could not start: {exc}") from exc

    root.title(f"ZDDV Debug Studio — {project.name}")
    root.geometry("1220x780")
    root.minsize(940, 600)

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
    evidence_tab = ttk.Frame(notebook, padding=8)
    tests_tab = ttk.Frame(notebook, padding=8)
    notebook.add(runs_tab, text="Runs")
    notebook.add(failures_tab, text="Failures")
    notebook.add(coverage_tab, text="Coverage")
    notebook.add(evidence_tab, text="Evidence")
    notebook.add(tests_tab, text="Per-test")

    def make_tree(parent, columns, headings, widths):
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for name, heading, width in zip(columns, headings, widths):
            tree.heading(name, text=heading)
            tree.column(name, width=width, anchor="w")
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
        (90, 220, 80, 120, 110, 430),
    )
    failures_tree = make_tree(
        failures_tab,
        ("count", "statuses", "tests", "signature"),
        ("Count", "Status", "Tests", "Normalized failure signature"),
        (80, 120, 260, 560),
    )
    coverage_tree = make_tree(
        coverage_tab,
        ("metric", "value", "counts"),
        ("Metric", "Coverage", "Explicit counts"),
        (220, 160, 360),
    )
    evidence_tree = make_tree(
        evidence_tab,
        ("kind", "status", "details", "source"),
        ("Evidence", "Status", "Details", "Snapshot / source"),
        (150, 130, 520, 300),
    )
    tests_tree = make_tree(
        tests_tab,
        ("test", "total", "passed", "failed", "timeout", "rate"),
        ("Test", "Total", "Pass", "Fail", "Timeout", "Pass rate"),
        (240, 80, 80, 80, 90, 110),
    )

    def clear(tree) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)

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
            duration = (
                "—"
                if row["duration_ms"] is None
                else f"{row['duration_ms']:.1f}"
            )
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
        if coverage is not None:
            for metric, value in coverage["breakdown"].items():
                if isinstance(value, dict):
                    rate = value.get("hit_rate")
                    coverage_text = (
                        "—" if rate is None else f"{float(rate):.1f}%"
                    )
                    total = value.get("total")
                    hit = value.get("hit")
                    counts_text = (
                        "—"
                        if total is None or hit is None
                        else f"{hit}/{total}"
                    )
                else:
                    coverage_text = f"{float(value):.1f}%"
                    counts = coverage["explicit_counts"].get(metric)
                    counts_text = "—" if counts is None else str(counts)
                coverage_tree.insert(
                    "",
                    "end",
                    values=(metric, coverage_text, counts_text),
                )

        clear(evidence_tree)
        assertions = snapshot["assertions"]
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
            evidence_tree.insert(
                "",
                "end",
                values=("Coverage", "NOT_PRESENT", "—", "—"),
            )
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
            evidence_tree.insert(
                "",
                "end",
                values=("Formal", "NOT_PRESENT", "—", "—"),
            )
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
            evidence_tree.insert(
                "",
                "end",
                values=("UVM", "NOT_PRESENT", "—", "—"),
            )
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

        clear(tests_tree)
        for row in stats.get("tests", []):
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

        project_info = snapshot["project"]
        status_var.set(
            f"Read-only · {project_info['simulator']} · top={project_info['top']} · "
            f"showing {len(snapshot['runs'])} persisted run(s)"
        )

    ttk.Label(
        outer,
        text=(
            "Display-only viewer: refresh reads persisted evidence. It does not run "
            "verification, invoke AI, or apply generated artifacts."
        ),
    ).pack(fill="x", pady=(10, 0))

    refresh()
    root.mainloop()
    return 0

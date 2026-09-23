from __future__ import annotations

from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import (
    assertion_statistics,
    list_assertion_events,
    list_coverage_score_snapshots,
    list_coverage_snapshots,
    list_formal_property_results,
    list_formal_result_snapshots,
    list_run_records,
    list_uvm_log_snapshots,
    list_uvm_report_messages,
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
    latest_formal = formal_rows[0] if formal_rows else None
    latest_uvm = uvm_rows[0] if uvm_rows else None

    formal_properties = (
        list_formal_property_results(
            project,
            str(latest_formal["snapshot_id"]),
            limit=limit,
        )
        if latest_formal is not None
        else []
    )
    uvm_messages = (
        list_uvm_report_messages(
            project,
            str(latest_uvm["snapshot_id"]),
            limit=limit,
        )
        if latest_uvm is not None
        else []
    )

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
        "assertion_events": list_assertion_events(project, limit=limit),
        "recent_runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": latest_formal,
        "formal_properties": formal_properties,
        "latest_uvm": latest_uvm,
        "uvm_messages": uvm_messages,
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
    window.geometry("1260x800")
    window.minsize(960, 620)

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
    assertion_tab = ttk.Frame(notebook, padding=8)
    formal_tab = ttk.Frame(notebook, padding=8)
    uvm_tab = ttk.Frame(notebook, padding=8)
    notebook.add(run_tab, text="Recent Runs")
    notebook.add(failure_tab, text="Failure Groups")
    notebook.add(evidence_tab, text="Evidence")
    notebook.add(assertion_tab, text="Assertions")
    notebook.add(formal_tab, text="Formal")
    notebook.add(uvm_tab, text="UVM")

    def _make_tree(
        parent,
        columns: tuple[str, ...],
        specs: tuple[tuple[str, str, int], ...],
    ):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        for column, title, width in specs:
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w")
        ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    run_tree = _make_tree(
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
    failure_tree = _make_tree(
        failure_tab,
        ("count", "status", "tests", "signature"),
        (
            ("count", "Count", 80),
            ("status", "Status", 120),
            ("tests", "Tests", 260),
            ("signature", "Normalized signature", 620),
        ),
    )
    evidence_tree = _make_tree(
        evidence_tab,
        ("kind", "status", "details", "id"),
        (
            ("kind", "Evidence", 150),
            ("status", "Status", 130),
            ("details", "Details", 580),
            ("id", "Snapshot / source", 300),
        ),
    )
    assertion_tree = _make_tree(
        assertion_tab,
        ("status", "name", "run", "line", "message"),
        (
            ("status", "Status", 90),
            ("name", "Assertion", 260),
            ("run", "Run ID", 280),
            ("line", "Log line", 90),
            ("message", "Message", 500),
        ),
    )
    formal_tree = _make_tree(
        formal_tab,
        ("status", "kind", "name", "interpretation", "depth", "message"),
        (
            ("status", "Status", 90),
            ("kind", "Kind", 90),
            ("name", "Property", 260),
            ("interpretation", "Interpretation", 180),
            ("depth", "Depth", 90),
            ("message", "Message", 430),
        ),
    )
    uvm_tree = _make_tree(
        uvm_tab,
        ("severity", "report_id", "component", "time", "line", "message"),
        (
            ("severity", "Severity", 120),
            ("report_id", "Report ID", 150),
            ("component", "Component", 260),
            ("time", "Time", 110),
            ("line", "Log line", 90),
            ("message", "Message", 440),
        ),
    )

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
        children = tree.get_children()
        if children:
            tree.delete(*children)

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

        _clear(assertion_tree)
        for event in current["assertion_events"]:
            assertion_tree.insert(
                "",
                "end",
                values=(
                    event["status"],
                    event["assertion_name"],
                    event["run_id"],
                    "-" if event["log_line"] is None else event["log_line"],
                    event["message"] or "",
                ),
            )

        _clear(formal_tree)
        for item in current["formal_properties"]:
            effective_depth = item["effective_depth"]
            depth = effective_depth if effective_depth is not None else item["depth"]
            formal_tree.insert(
                "",
                "end",
                values=(
                    item["status"],
                    item["kind"],
                    item["name"],
                    item["interpretation"] or "",
                    "-" if depth is None else depth,
                    item["message"] or "",
                ),
            )

        _clear(uvm_tree)
        for message in current["uvm_messages"]:
            uvm_tree.insert(
                "",
                "end",
                values=(
                    message["severity"],
                    message["report_id"] or "",
                    message["component"] or "",
                    message["time_text"] or "",
                    message["log_line"],
                    message["message"] or "",
                ),
            )

        status_text.set(
            f"{project.simulator} · top={project.top} · "
            f"showing up to {limit} records per detail pane"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

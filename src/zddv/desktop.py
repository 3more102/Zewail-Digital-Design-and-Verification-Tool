from __future__ import annotations

import json
from typing import Any

from zddv.config import ProjectConfig
from zddv.design_index import build_design_index
from zddv.desktop_waveform import attach_desktop_waveform_tab
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

    instances = payload.get("instances") if isinstance(payload, dict) else None
    if not isinstance(instances, list):
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
        "instances": instances,
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
        "assertion_events": list_assertion_events(project, limit=limit),
        "recent_runs": runs,
        "failure_groups": group_failure_records(runs),
        "latest_coverage": _latest_coverage(project),
        "latest_formal": latest_formal,
        "formal_properties": formal_properties,
        "latest_uvm": latest_uvm,
        "uvm_messages": uvm_messages,
        "design": design,
        "elaborated_hierarchy": _load_persisted_elaborated_hierarchy(project),
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
    sources_tab = ttk.Frame(notebook, padding=8)
    hierarchy_tab = ttk.Frame(notebook, padding=8)
    elaborated_tab = ttk.Frame(notebook, padding=8)
    notebook.add(run_tab, text="Recent Runs")
    notebook.add(failure_tab, text="Failure Groups")
    notebook.add(evidence_tab, text="Evidence")
    notebook.add(sources_tab, text="Sources")
    notebook.add(hierarchy_tab, text="Hierarchy")
    assertions_tab = ttk.Frame(notebook, padding=8)
    formal_tab = ttk.Frame(notebook, padding=8)
    uvm_tab = ttk.Frame(notebook, padding=8)
    notebook.add(assertions_tab, text="Assertions")
    notebook.add(formal_tab, text="Formal")
    notebook.add(uvm_tab, text="UVM")
    notebook.add(elaborated_tab, text="Elaborated")
    attach_desktop_waveform_tab(notebook, project)

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

    source_columns = ("kind", "location", "details")
    source_tree = ttk.Treeview(
        sources_tab,
        columns=source_columns,
        show="tree headings",
    )
    source_tree.heading("#0", text="Source / unit")
    source_tree.column("#0", width=320, anchor="w")
    for column, title, width in (
        ("kind", "Kind", 100),
        ("location", "Location", 260),
        ("details", "Evidence", 460),
    ):
        source_tree.heading(column, text=title)
        source_tree.column(column, width=width, anchor="w")
    source_tree.pack(fill="both", expand=True)

    hierarchy_columns = ("type", "source", "state")
    hierarchy_tree = ttk.Treeview(
        hierarchy_tab,
        columns=hierarchy_columns,
        show="tree headings",
    )
    hierarchy_tree.heading("#0", text="Instance")
    hierarchy_tree.column("#0", width=320, anchor="w")
    for column, title, width in (
        ("type", "Type", 220),
        ("source", "Source", 320),
        ("state", "State", 120),
    ):
        hierarchy_tree.heading(column, text=title)
        hierarchy_tree.column(column, width=width, anchor="w")
    hierarchy_tree.pack(fill="both", expand=True)


    elaborated_columns = ("module", "source", "state")
    elaborated_tree = ttk.Treeview(
        elaborated_tab,
        columns=elaborated_columns,
        show="tree headings",
    )
    elaborated_tree.heading("#0", text="Hierarchy path")
    elaborated_tree.column("#0", width=420, anchor="w")
    for column, title, width in (
        ("module", "Module", 220),
        ("source", "Source", 320),
        ("state", "State", 120),
    ):
        elaborated_tree.heading(column, text=title)
        elaborated_tree.column(column, width=width, anchor="w")
    elaborated_tree.pack(fill="both", expand=True)

    assertion_columns = ("status", "name", "run", "line", "message")
    assertion_tree = ttk.Treeview(assertions_tab, columns=assertion_columns, show="headings")
    for column, title, width in (
        ("status", "Status", 90),
        ("name", "Assertion", 240),
        ("run", "Run ID", 260),
        ("line", "Log line", 90),
        ("message", "Message", 480),
    ):
        assertion_tree.heading(column, text=title)
        assertion_tree.column(column, width=width, anchor="w")
    assertion_tree.pack(fill="both", expand=True)

    formal_columns = ("status", "kind", "name", "interpretation", "depth", "message")
    formal_tree = ttk.Treeview(formal_tab, columns=formal_columns, show="headings")
    for column, title, width in (
        ("status", "Status", 90),
        ("kind", "Kind", 90),
        ("name", "Property", 240),
        ("interpretation", "Interpretation", 170),
        ("depth", "Depth", 90),
        ("message", "Message", 390),
    ):
        formal_tree.heading(column, text=title)
        formal_tree.column(column, width=width, anchor="w")
    formal_tree.pack(fill="both", expand=True)

    uvm_columns = ("severity", "report_id", "component", "time", "line", "message")
    uvm_tree = ttk.Treeview(uvm_tab, columns=uvm_columns, show="headings")
    for column, title, width in (
        ("severity", "Severity", 120),
        ("report_id", "Report ID", 140),
        ("component", "Component", 230),
        ("time", "Time", 100),
        ("line", "Log line", 90),
        ("message", "Message", 400),
    ):
        uvm_tree.heading(column, text=title)
        uvm_tree.column(column, width=width, anchor="w")
    uvm_tree.pack(fill="both", expand=True)

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


        design = current["design"]
        units_by_file: dict[str, list[dict[str, Any]]] = {}
        for unit in design["units"]:
            units_by_file.setdefault(unit["file"], []).append(unit)

        _clear(source_tree)
        for source in design["files"]:
            file_id = source_tree.insert(
                "",
                "end",
                text=source["path"],
                values=(
                    "file",
                    "",
                    f"{source['lines']} lines · {source['bytes']} bytes · "
                    f"sha256={source['sha256']}",
                ),
                open=True,
            )
            for unit in units_by_file.get(source["path"], []):
                source_tree.insert(
                    file_id,
                    "end",
                    text=unit["name"],
                    values=(
                        unit["kind"],
                        f"{unit['file']}:{unit['line']}",
                        f"lines {unit['line']}-{unit['end_line']} · "
                        f"{len(unit['instances'])} child instances",
                    ),
                )

        _clear(hierarchy_tree)

        def _insert_hierarchy(parent: str, node: dict[str, Any]) -> None:
            if not node.get("resolved", True):
                state = "UNRESOLVED"
            elif node.get("recursive"):
                state = "RECURSIVE"
            else:
                state = "RESOLVED"

            source = "-"
            if node.get("file") is not None and node.get("line") is not None:
                source = f"{node['file']}:{node['line']}"

            item_id = hierarchy_tree.insert(
                parent,
                "end",
                text=node["instance"],
                values=(node["type"], source, state),
                open=True,
            )
            for child in node.get("children", []):
                _insert_hierarchy(item_id, child)

        _insert_hierarchy("", design["hierarchy"])

        _clear(elaborated_tree)
        elaborated = current["elaborated_hierarchy"]
        if elaborated["status"] == "PRESENT":
            for row in elaborated["instances"]:
                location = row.get("location") or {}
                source = "-"
                if location.get("path"):
                    source = str(location["path"])
                    if location.get("line"):
                        source += f":{location['line']}"
                state = "TOP" if row.get("top") else "INSTANCE"
                elaborated_tree.insert(
                    "",
                    "end",
                    text=row.get("path") or row.get("name") or "-",
                    values=(row.get("module") or "-", source, state),
                )
        else:
            elaborated_tree.insert(
                "",
                "end",
                text=elaborated["status"],
                values=(
                    "-",
                    elaborated.get("error") or elaborated["path"],
                    elaborated["status"],
                ),
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
            depth = (
                item["effective_depth"]
                if item["effective_depth"] is not None
                else item["depth"]
            )
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
            f"showing {len(current['recent_runs'])} runs · "
            f"{design['summary']['files']} source files · "
            f"{design['summary']['instances']} instances"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

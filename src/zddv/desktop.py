from __future__ import annotations

from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.design_index import build_design_index
from zddv.debug_probes import suggest_debug_probes
from zddv.storage import (
    assertion_statistics,
    get_run_record,
    list_coverage_score_snapshots,
    list_coverage_snapshots,
    list_formal_result_snapshots,
    list_run_records,
    list_uvm_log_snapshots,
    run_statistics,
)
from zddv.triage import group_failure_records
from zddv.waveform import build_waveform_index


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


def build_waveform_navigation_snapshot(
    project: ProjectConfig,
    *,
    run_id: str,
) -> dict[str, Any]:
    """Build read-only waveform/probe state for one persisted verification run."""
    row = get_run_record(project, run_id)
    if row is None:
        raise ValueError(f"Run '{run_id}' was not found in the verification database.")

    probe_report: dict[str, Any] | None = None
    probe_error: str | None = None
    if row["status"] in {"FAIL", "TIMEOUT"}:
        try:
            probe_report = suggest_debug_probes(project, run_id=run_id)
        except (RuntimeError, ValueError) as exc:
            probe_error = str(exc)

    result: dict[str, Any] = {
        "run": {
            "run_id": row["run_id"],
            "status": row["status"],
            "test_name": row["test_name"],
            "seed": row["seed"],
            "waveform_path": row["waveform_path"],
        },
        "waveform_state": "NOT_PRESENT",
        "waveform": None,
        "probe_report": probe_report,
        "probe_error": probe_error,
        "note": None,
    }

    waveform_value = row.get("waveform_path")
    if not waveform_value:
        result["note"] = "Run has no recorded waveform artifact."
        return result

    waveform_path = Path(str(waveform_value)).resolve()
    if not waveform_path.exists():
        result["waveform_state"] = "MISSING"
        result["note"] = f"Recorded waveform does not exist: {waveform_path}"
        return result

    try:
        waveform = build_waveform_index(
            waveform_path,
            run_id=str(row["run_id"]),
            project_name=project.name,
        )
    except RuntimeError as exc:
        result["waveform_state"] = "UNSUPPORTED"
        result["note"] = str(exc)
        return result

    result["waveform_state"] = (
        "INDEXED" if waveform["parse_status"] == "indexed" else "METADATA_ONLY"
    )
    result["waveform"] = waveform
    return result


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
    sources_tab = ttk.Frame(notebook, padding=8)
    hierarchy_tab = ttk.Frame(notebook, padding=8)
    waveform_tab = ttk.Frame(notebook, padding=8)
    notebook.add(run_tab, text="Recent Runs")
    notebook.add(failure_tab, text="Failure Groups")
    notebook.add(evidence_tab, text="Evidence")
    notebook.add(sources_tab, text="Sources")
    notebook.add(hierarchy_tab, text="Hierarchy")
    notebook.add(waveform_tab, text="Waveform / Probes")

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


    waveform_status = tk.StringVar(value="Select a run to inspect its recorded waveform.")
    ttk.Label(waveform_tab, textvariable=waveform_status).pack(fill="x", pady=(0, 8))

    waveform_pane = ttk.Panedwindow(waveform_tab, orient="vertical")
    waveform_pane.pack(fill="both", expand=True)
    signal_frame = ttk.LabelFrame(waveform_pane, text="Recorded waveform signals", padding=6)
    probe_frame = ttk.LabelFrame(
        waveform_pane,
        text="Evidence-backed targeted probes",
        padding=6,
    )
    waveform_pane.add(signal_frame, weight=3)
    waveform_pane.add(probe_frame, weight=2)

    signal_columns = ("scope", "name", "width", "path")
    signal_tree = ttk.Treeview(signal_frame, columns=signal_columns, show="headings")
    for column, title, width in (
        ("scope", "Scope", 250),
        ("name", "Signal", 180),
        ("width", "Width", 80),
        ("path", "Hierarchical path", 520),
    ):
        signal_tree.heading(column, text=title)
        signal_tree.column(column, width=width, anchor="w")
    signal_tree.pack(fill="both", expand=True)

    probe_columns = ("rank", "signal", "evidence", "reason")
    probe_tree = ttk.Treeview(probe_frame, columns=probe_columns, show="headings")
    for column, title, width in (
        ("rank", "Rank", 70),
        ("signal", "Signal", 320),
        ("evidence", "Evidence", 220),
        ("reason", "Reason", 500),
    ):
        probe_tree.heading(column, text=title)
        probe_tree.column(column, width=width, anchor="w")
    probe_tree.pack(fill="both", expand=True)

    probe_status = tk.StringVar(
        value="Probe suggestions are display-only and never auto-executed."
    )
    ttk.Label(probe_frame, textvariable=probe_status).pack(fill="x", pady=(6, 0))

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

    def _load_waveform_run(run_id: str) -> None:
        state = build_waveform_navigation_snapshot(project, run_id=run_id)
        waveform = state["waveform"]
        _clear(signal_tree)
        _clear(probe_tree)

        if waveform is None:
            waveform_status.set(
                f"{run_id} · {state['waveform_state']} · "
                f"{state['note'] or 'no indexed waveform data'}"
            )
        else:
            summary = waveform["summary"]
            waveform_status.set(
                f"{run_id} · {state['waveform_state']} · "
                f"{waveform['format'].upper()} · "
                f"{summary['signals']} signals · {summary['scopes']} scopes · "
                f"timescale={waveform.get('timescale') or '-'}"
            )
            for signal in waveform.get("signals", []):
                signal_tree.insert(
                    "",
                    "end",
                    values=(
                        signal.get("scope") or "-",
                        signal.get("name") or "-",
                        signal.get("width") or "-",
                        signal.get("path") or "-",
                    ),
                )

        report = state["probe_report"]
        if report is not None:
            for suggestion in report.get("suggestions", []):
                probe_tree.insert(
                    "",
                    "end",
                    values=(
                        suggestion.get("rank", "-"),
                        suggestion.get("signal", "-"),
                        f"{suggestion.get('source_candidate_kind', '-')} · "
                        f"score={suggestion.get('source_evidence_score', '-')}",
                        suggestion.get("reason", "-"),
                    ),
                )
            blockers = [
                str(item.get("code", "BLOCKED"))
                for item in report.get("blockers", [])
            ]
            if blockers:
                probe_status.set(
                    "No invented probes · blockers: " + ", ".join(blockers)
                )
            else:
                probe_status.set(
                    f"{len(report.get('suggestions', []))} evidence-backed probe "
                    "suggestion(s) · display-only; never auto-executed"
                )
        elif state["probe_error"]:
            probe_status.set(f"Probe analysis unavailable: {state['probe_error']}")
        else:
            probe_status.set(
                "Probe suggestions are shown only for FAIL/TIMEOUT runs with "
                "explicit failure evidence."
            )

    def _on_run_select(_event=None) -> None:
        selected = run_tree.selection()
        if not selected:
            return
        values = run_tree.item(selected[0], "values")
        if len(values) >= 5:
            _load_waveform_run(str(values[4]))

    run_tree.bind("<<TreeviewSelect>>", _on_run_select)

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

        run_items = run_tree.get_children()
        if run_items:
            run_tree.selection_set(run_items[0])
            _load_waveform_run(str(run_tree.item(run_items[0], "values")[4]))
        else:
            _clear(signal_tree)
            _clear(probe_tree)
            waveform_status.set("No persisted run is available for waveform navigation.")
            probe_status.set(
                "Probe suggestions require a persisted FAIL/TIMEOUT run with evidence."
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

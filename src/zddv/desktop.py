from __future__ import annotations

from pathlib import Path
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
from zddv.waveform import build_waveform_index, select_waveform_run
from zddv.waveform_probe import probe_vcd_signals


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
    }


def build_desktop_waveform_snapshot(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build an in-memory waveform navigation model without writing artifacts."""
    run = select_waveform_run(project, run_id=run_id)
    waveform_path = Path(str(run["waveform_path"])).resolve()
    index = build_waveform_index(
        waveform_path,
        run_id=str(run["run_id"]),
        project_name=project.name,
    )
    return {
        "run_id": str(run["run_id"]),
        "test_name": run.get("test_name"),
        "status": str(run["status"]),
        "path": str(waveform_path),
        "format": str(index["format"]),
        "parse_status": str(index["parse_status"]),
        "timescale": index.get("timescale"),
        "artifact": dict(index["artifact"]),
        "summary": dict(index["summary"]),
        "scopes": list(index.get("scopes", [])),
        "signals": list(index.get("signals", [])),
        "note": index.get("note"),
    }


def probe_desktop_waveform_signal(
    project: ProjectConfig,
    signal: str,
    *,
    run_id: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    max_changes: int = 500,
) -> dict[str, Any]:
    """Probe one recorded VCD signal in memory without persisting a probe report."""
    navigation = build_desktop_waveform_snapshot(project, run_id=run_id)
    if navigation["format"] != "vcd" or navigation["parse_status"] != "indexed":
        raise RuntimeError(
            "Desktop waveform probing currently requires a recorded indexed VCD artifact."
        )

    result = probe_vcd_signals(
        navigation["path"],
        [signal],
        start_time=start_time,
        end_time=end_time,
        max_changes=max_changes,
    )
    result["run_id"] = navigation["run_id"]
    result["test_name"] = navigation["test_name"]
    return result


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
    waveform_tab = ttk.Frame(notebook, padding=8)
    evidence_tab = ttk.Frame(notebook, padding=8)
    notebook.add(run_tab, text="Recent Runs")
    notebook.add(failure_tab, text="Failure Groups")
    notebook.add(waveform_tab, text="Waveform")
    notebook.add(evidence_tab, text="Evidence")

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

    waveform_info_var = tk.StringVar(value="No recorded waveform selected.")
    ttk.Label(
        waveform_tab,
        textvariable=waveform_info_var,
        anchor="w",
    ).pack(fill="x", pady=(0, 6))

    waveform_signal_tree = ttk.Treeview(
        waveform_tab,
        columns=("path", "width", "type"),
        show="headings",
        height=10,
        selectmode="browse",
    )
    for column, title, width in (
        ("path", "Signal", 620),
        ("width", "Width", 80),
        ("type", "VCD type", 120),
    ):
        waveform_signal_tree.heading(column, text=title)
        waveform_signal_tree.column(column, width=width, anchor="w")
    waveform_signal_tree.pack(fill="both", expand=True)

    probe_controls = ttk.Frame(waveform_tab, padding=(0, 8, 0, 6))
    probe_controls.pack(fill="x")
    probe_start_var = tk.StringVar(value="")
    probe_end_var = tk.StringVar(value="")
    probe_max_var = tk.StringVar(value="500")
    ttk.Label(probe_controls, text="Start").pack(side="left")
    ttk.Entry(probe_controls, textvariable=probe_start_var, width=10).pack(
        side="left", padx=(4, 10)
    )
    ttk.Label(probe_controls, text="End").pack(side="left")
    ttk.Entry(probe_controls, textvariable=probe_end_var, width=10).pack(
        side="left", padx=(4, 10)
    )
    ttk.Label(probe_controls, text="Max changes").pack(side="left")
    ttk.Entry(probe_controls, textvariable=probe_max_var, width=8).pack(
        side="left", padx=(4, 10)
    )

    waveform_probe_tree = ttk.Treeview(
        waveform_tab,
        columns=("time", "value"),
        show="headings",
        height=8,
    )
    waveform_probe_tree.heading("time", text="Time")
    waveform_probe_tree.heading("value", text="Value")
    waveform_probe_tree.column("time", width=180, anchor="w")
    waveform_probe_tree.column("value", width=700, anchor="w")
    waveform_probe_tree.pack(fill="both", expand=True)

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

    footer = ttk.Frame(container, padding=(0, 10, 0, 0))
    footer.pack(fill="x")
    ttk.Label(
        footer,
        text=(
            "Display-only viewer: refresh reads persisted evidence; waveform browsing "
            "and probing parse recorded artifacts in memory and do not run verification, "
            "invoke AI, or apply generated artifacts."
        ),
    ).pack(side="left")

    current: dict[str, Any] = {}
    waveform_current: dict[str, Any] | None = None

    def _clear(tree) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _optional_int(value: str, label: str) -> int | None:
        text = value.strip()
        if not text:
            return None
        try:
            parsed = int(text)
        except ValueError as exc:
            raise ValueError(f"{label} must be an integer") from exc
        if parsed < 0:
            raise ValueError(f"{label} must be >= 0")
        return parsed

    def probe_selected_waveform() -> None:
        if waveform_current is None:
            waveform_info_var.set("No recorded waveform is available to probe.")
            return
        selection = waveform_signal_tree.selection()
        if not selection:
            waveform_info_var.set("Select one waveform signal before probing.")
            return

        values = waveform_signal_tree.item(selection[0], "values")
        signal_path = str(values[0])
        try:
            start_time = _optional_int(probe_start_var.get(), "Start time")
            end_time = _optional_int(probe_end_var.get(), "End time")
            max_changes = int(probe_max_var.get().strip() or "500")
            result = probe_desktop_waveform_signal(
                project,
                signal_path,
                run_id=waveform_current["run_id"],
                start_time=start_time,
                end_time=end_time,
                max_changes=max_changes,
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            waveform_info_var.set(f"Probe error: {exc}")
            return

        _clear(waveform_probe_tree)
        signal = result["signals"][0]
        for change in signal["changes"]:
            waveform_probe_tree.insert(
                "",
                "end",
                values=(change["time"], change["value"]),
            )
        suffix = " (truncated)" if signal["truncated"] else ""
        waveform_info_var.set(
            f"{result['run_id']} · {signal_path} · "
            f"{len(signal['changes'])} change(s){suffix} · "
            f"timescale={result.get('timescale') or 'unknown'}"
        )

    ttk.Button(
        probe_controls,
        text="Probe selected",
        command=probe_selected_waveform,
    ).pack(side="left")

    def refresh() -> None:
        nonlocal current, waveform_current
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

        _clear(waveform_signal_tree)
        _clear(waveform_probe_tree)
        try:
            waveform_current = build_desktop_waveform_snapshot(project)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            waveform_current = None
            waveform_info_var.set(f"No usable recorded waveform: {exc}")
        else:
            summary = waveform_current["summary"]
            waveform_info_var.set(
                f"{waveform_current['run_id']} · "
                f"{waveform_current['format'].upper()} · "
                f"{summary['signals']} signal(s) · "
                f"{summary['scopes']} scope(s) · "
                f"timescale={waveform_current.get('timescale') or 'unknown'}"
            )
            for signal in waveform_current["signals"]:
                waveform_signal_tree.insert(
                    "",
                    "end",
                    values=(
                        signal["path"],
                        signal["width"],
                        signal["var_type"],
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
            f"showing {len(current['recent_runs'])} runs"
        )

    ttk.Button(header, text="Refresh", command=refresh).pack(side="right", padx=(0, 12))
    refresh()
    window.mainloop()
    return current

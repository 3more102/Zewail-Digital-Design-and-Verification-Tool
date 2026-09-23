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
    get_run_record,
    list_formal_result_snapshots,
    list_run_records,
    list_uvm_log_snapshots,
    run_statistics,
)
from zddv.triage import group_failure_records
from zddv.waveform import build_waveform_index
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
        "elaborated_hierarchy": _load_persisted_elaborated_hierarchy(project),
    }


def _recorded_waveform_path(project: ProjectConfig, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    return path.resolve()


def build_desktop_waveform_snapshot(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build read-only waveform navigation state without writing artifacts."""
    if run_id is not None:
        run = get_run_record(project, run_id)
        if run is None:
            raise ValueError(f"Run '{run_id}' was not found in the verification database.")
        candidates = [run]
    else:
        candidates = list_run_records(project, limit=1_000_000)

    selected: dict[str, Any] | None = None
    waveform_path: Path | None = None
    for candidate in candidates:
        value = candidate.get("waveform_path")
        if not value:
            continue
        resolved = _recorded_waveform_path(project, str(value))
        if not resolved.exists():
            if run_id is not None:
                raise RuntimeError(
                    f"Recorded waveform for run '{run_id}' does not exist: {resolved}"
                )
            continue
        selected = candidate
        waveform_path = resolved
        break

    if selected is None or waveform_path is None:
        if run_id is not None:
            raise RuntimeError(f"Run '{run_id}' has no recorded waveform artifact.")
        raise RuntimeError("No persisted run with an available waveform artifact was found.")

    index = build_waveform_index(
        waveform_path,
        run_id=str(selected["run_id"]),
        project_name=project.name,
    )
    return {
        "run_id": str(selected["run_id"]),
        "test_name": selected.get("test_name"),
        "status": str(selected["status"]),
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
    run_id: str,
    start_time: int | None = None,
    end_time: int | None = None,
    max_changes: int = 500,
) -> dict[str, Any]:
    """Inspect recorded VCD changes in memory; never write or execute a command."""
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
    sources_tab = ttk.Frame(notebook, padding=8)
    hierarchy_tab = ttk.Frame(notebook, padding=8)
    elaborated_tab = ttk.Frame(notebook, padding=8)
    notebook.add(run_tab, text="Recent Runs")
    notebook.add(failure_tab, text="Failure Groups")
    notebook.add(waveform_tab, text="Waveform")
    notebook.add(evidence_tab, text="Evidence")
    notebook.add(sources_tab, text="Sources")
    notebook.add(hierarchy_tab, text="Hierarchy")
    notebook.add(elaborated_tab, text="Elaborated")

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
    ttk.Label(waveform_tab, textvariable=waveform_info_var, anchor="w").pack(
        fill="x", pady=(0, 6)
    )

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
    waveform_run_id: str | None = None

    def _clear(tree) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _load_waveform(run_id: str | None = None) -> None:
        nonlocal waveform_run_id
        _clear(waveform_signal_tree)
        _clear(waveform_probe_tree)
        try:
            state = build_desktop_waveform_snapshot(project, run_id=run_id)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            waveform_run_id = None
            waveform_info_var.set(str(exc))
            return

        waveform_run_id = state["run_id"]
        summary = state["summary"]
        waveform_info_var.set(
            f"run={state['run_id']} · {state['format'].upper()} · "
            f"{state['parse_status']} · {summary['signals']} signal(s) · "
            f"timescale={state.get('timescale') or '-'}"
        )
        for signal in state["signals"]:
            waveform_signal_tree.insert(
                "",
                "end",
                values=(
                    signal.get("path") or "-",
                    signal.get("width") or "-",
                    signal.get("var_type") or "-",
                ),
            )

    def _optional_nonnegative_int(value: str, label: str) -> int | None:
        value = value.strip()
        if not value:
            return None
        parsed = int(value)
        if parsed < 0:
            raise ValueError(f"{label} must be >= 0")
        return parsed

    def _probe_selected_signal() -> None:
        if waveform_run_id is None:
            waveform_info_var.set("Select a persisted run with an indexed VCD waveform.")
            return
        selected = waveform_signal_tree.selection()
        if not selected:
            waveform_info_var.set("Select one waveform signal to inspect.")
            return
        values = waveform_signal_tree.item(selected[0], "values")
        signal = str(values[0])
        try:
            start_time = _optional_nonnegative_int(probe_start_var.get(), "Start time")
            end_time = _optional_nonnegative_int(probe_end_var.get(), "End time")
            max_changes = int(probe_max_var.get().strip())
            result = probe_desktop_waveform_signal(
                project,
                signal,
                run_id=waveform_run_id,
                start_time=start_time,
                end_time=end_time,
                max_changes=max_changes,
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            waveform_info_var.set(str(exc))
            return

        _clear(waveform_probe_tree)
        entry = result["signals"][0]
        for change in entry["changes"]:
            waveform_probe_tree.insert(
                "",
                "end",
                values=(change["time"], change["value"]),
            )
        suffix = " · truncated" if entry["truncated"] else ""
        waveform_info_var.set(
            f"run={waveform_run_id} · {entry['path']} · "
            f"{len(entry['changes'])} change(s){suffix}"
        )

    def _on_run_select(_event=None) -> None:
        selected = run_tree.selection()
        if not selected:
            return
        values = run_tree.item(selected[0], "values")
        if len(values) >= 5:
            _load_waveform(str(values[4]))

    ttk.Button(
        probe_controls,
        text="Inspect selected signal",
        command=_probe_selected_signal,
    ).pack(side="left")
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

        try:
            _load_waveform()
        except Exception:
            # _load_waveform already normalizes expected read-only failures.
            pass

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

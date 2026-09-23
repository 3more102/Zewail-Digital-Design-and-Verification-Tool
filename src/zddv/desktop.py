from __future__ import annotations

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


def _resolve_waveform_path(project: ProjectConfig, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    return path.resolve()


def _project_relative_path(project: ProjectConfig, path: Path) -> str:
    try:
        return str(path.relative_to(project.root.resolve()))
    except ValueError:
        return str(path)


def _waveform_artifacts(
    project: ProjectConfig,
    runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for row in runs:
        value = row.get("waveform_path")
        if not value:
            continue
        path = _resolve_waveform_path(project, str(value))
        exists = path.is_file()
        artifacts.append(
            {
                "run_id": row["run_id"],
                "created_at": row["created_at"],
                "test_name": row.get("test_name"),
                "status": row["status"],
                "simulator": row["simulator"],
                "format": path.suffix.lower().lstrip(".") or "unknown",
                "path": str(path),
                "project_path": _project_relative_path(project, path),
                "exists": exists,
                "bytes": path.stat().st_size if exists else None,
            }
        )
    return artifacts


def _waveform_run(
    project: ProjectConfig,
    run_id: str,
) -> tuple[dict[str, Any], Path]:
    row = get_run_record(project, run_id)
    if row is None:
        raise RuntimeError(f"Run '{run_id}' was not found in the verification database.")
    value = row.get("waveform_path")
    if not value:
        raise RuntimeError(f"Run '{run_id}' has no recorded waveform artifact.")
    path = _resolve_waveform_path(project, str(value))
    if not path.is_file():
        raise RuntimeError(
            f"Recorded waveform for run '{run_id}' does not exist: {path}"
        )
    return row, path


def build_desktop_waveform_index(
    project: ProjectConfig,
    run_id: str,
) -> dict[str, Any]:
    """Read and index one recorded waveform without materializing index artifacts."""
    row, path = _waveform_run(project, run_id)
    index = build_waveform_index(
        path,
        run_id=str(row["run_id"]),
        project_name=project.name,
    )
    index["artifact"]["project_path"] = _project_relative_path(project, path)
    return index


def probe_desktop_waveform(
    project: ProjectConfig,
    run_id: str,
    signal: str,
    *,
    start_time: int | None = None,
    end_time: int | None = None,
    max_changes: int = 1_000,
) -> dict[str, Any]:
    """Read VCD signal changes for the desktop viewer without writing probe artifacts."""
    query = signal.strip()
    if not query:
        raise ValueError("signal must not be empty")

    row, path = _waveform_run(project, run_id)
    result = probe_vcd_signals(
        path,
        [query],
        start_time=start_time,
        end_time=end_time,
        max_changes=max_changes,
    )
    result["project"] = project.name
    result["run_id"] = str(row["run_id"])
    result["artifact"]["project_path"] = _project_relative_path(project, path)
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
        "waveforms": _waveform_artifacts(project, runs),
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
    notebook.add(run_tab, text="Recent Runs")
    notebook.add(failure_tab, text="Failure Groups")
    notebook.add(evidence_tab, text="Evidence")
    notebook.add(sources_tab, text="Sources")
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

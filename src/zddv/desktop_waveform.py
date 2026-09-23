from __future__ import annotations

from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.waveform import build_waveform_index, select_waveform_run
from zddv.waveform_probe import probe_vcd_signals


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



def attach_desktop_waveform_tab(notebook: Any, project: ProjectConfig) -> None:
    """Attach a self-contained read-only waveform navigation tab to a Tk notebook."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Desktop waveform navigation requires Python with tkinter support installed."
        ) from exc

    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Waveform")

    info_var = tk.StringVar(value="No recorded waveform selected.")
    ttk.Label(tab, textvariable=info_var, anchor="w").pack(fill="x", pady=(0, 6))

    signal_tree = ttk.Treeview(
        tab,
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
        signal_tree.heading(column, text=title)
        signal_tree.column(column, width=width, anchor="w")
    signal_tree.pack(fill="both", expand=True)

    controls = ttk.Frame(tab, padding=(0, 8, 0, 6))
    controls.pack(fill="x")
    start_var = tk.StringVar(value="")
    end_var = tk.StringVar(value="")
    max_var = tk.StringVar(value="500")

    ttk.Label(controls, text="Start").pack(side="left")
    ttk.Entry(controls, textvariable=start_var, width=10).pack(
        side="left", padx=(4, 10)
    )
    ttk.Label(controls, text="End").pack(side="left")
    ttk.Entry(controls, textvariable=end_var, width=10).pack(
        side="left", padx=(4, 10)
    )
    ttk.Label(controls, text="Max changes").pack(side="left")
    ttk.Entry(controls, textvariable=max_var, width=8).pack(
        side="left", padx=(4, 10)
    )

    probe_tree = ttk.Treeview(
        tab,
        columns=("time", "value"),
        show="headings",
        height=8,
    )
    probe_tree.heading("time", text="Time")
    probe_tree.heading("value", text="Value")
    probe_tree.column("time", width=180, anchor="w")
    probe_tree.column("value", width=700, anchor="w")
    probe_tree.pack(fill="both", expand=True)

    state: dict[str, Any] = {"waveform": None}

    def clear(tree: Any) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)

    def optional_int(value: str, label: str) -> int | None:
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

    def refresh_waveform() -> None:
        clear(signal_tree)
        clear(probe_tree)
        try:
            navigation = build_desktop_waveform_snapshot(project)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            state["waveform"] = None
            info_var.set(f"No usable recorded waveform: {exc}")
            return

        state["waveform"] = navigation
        summary = navigation["summary"]
        info_var.set(
            f"{navigation['run_id']} · {navigation['format'].upper()} · "
            f"{summary['signals']} signal(s) · {summary['scopes']} scope(s) · "
            f"timescale={navigation.get('timescale') or 'unknown'}"
        )
        for signal in navigation["signals"]:
            signal_tree.insert(
                "",
                "end",
                values=(
                    signal["path"],
                    signal["width"],
                    signal["var_type"],
                ),
            )

    def probe_selected() -> None:
        navigation = state["waveform"]
        if navigation is None:
            info_var.set("No recorded waveform is available to probe.")
            return

        selection = signal_tree.selection()
        if not selection:
            info_var.set("Select one waveform signal before probing.")
            return

        values = signal_tree.item(selection[0], "values")
        signal_path = str(values[0])
        try:
            start_time = optional_int(start_var.get(), "Start time")
            end_time = optional_int(end_var.get(), "End time")
            max_changes = int(max_var.get().strip() or "500")
            result = probe_desktop_waveform_signal(
                project,
                signal_path,
                run_id=navigation["run_id"],
                start_time=start_time,
                end_time=end_time,
                max_changes=max_changes,
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            info_var.set(f"Probe error: {exc}")
            return

        clear(probe_tree)
        signal = result["signals"][0]
        for change in signal["changes"]:
            probe_tree.insert(
                "",
                "end",
                values=(change["time"], change["value"]),
            )
        suffix = " (truncated)" if signal["truncated"] else ""
        info_var.set(
            f"{result['run_id']} · {signal_path} · "
            f"{len(signal['changes'])} change(s){suffix} · "
            f"timescale={result.get('timescale') or 'unknown'}"
        )

    ttk.Button(controls, text="Refresh waveform", command=refresh_waveform).pack(
        side="right"
    )
    ttk.Button(controls, text="Probe selected", command=probe_selected).pack(
        side="left"
    )

    refresh_waveform()

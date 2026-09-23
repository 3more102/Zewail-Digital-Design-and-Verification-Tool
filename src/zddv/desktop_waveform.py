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

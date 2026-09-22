from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.formal.counterexample import ingest_formal_counterexample
from zddv.formal.results import analyze_formal_result_file
from zddv.formal.vcd_trace import ingest_formal_vcd_trace
from zddv.storage import (
    database_path,
    list_formal_property_results,
    list_formal_trace_signals,
    list_formal_trace_snapshots,
    list_formal_trace_steps,
)


def _trace_payload() -> dict:
    return {
        "source": "unit-formal",
        "property": "top.p_req_ack",
        "property_kind": "assert",
        "time_unit": "1 ns",
        "signals": [
            {"name": "top.clk", "width": 1, "metadata": {"scope": "top"}},
            {"name": "top.req", "width": 1, "metadata": {"scope": "top"}},
        ],
        "steps": [
            {
                "step": 0,
                "time": 0,
                "cycle": 0,
                "values": {"top.clk": "0", "top.req": "1"},
                "metadata": {"phase": "initial"},
            },
            {
                "step": 2,
                "time": 5,
                "cycle": 1,
                "values": {"top.clk": "1", "top.req": "1"},
            },
            {
                "step": 4,
                "time": 10,
                "cycle": 2,
                "values": {"top.clk": "0"},
            },
        ],
        "metadata": {"engine": "unit"},
    }


def _write_vcd(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """$timescale 1 ns $end
$scope module top $end
$var wire 1 ! clk $end
$var wire 1 " bad $end
$upscope $end
$enddefinitions $end
#0
0!
0"
#5
1!
1"
""",
        encoding="utf-8",
    )


def test_ingested_formal_trace_persists_snapshot_signals_and_steps(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "counterexample.json"
    source.write_text(json.dumps(_trace_payload()), encoding="utf-8")

    record = ingest_formal_counterexample(project, source)

    assert len(record["trace_id"]) == 32
    assert record["created_at"]
    assert database_path(project).is_file()

    snapshots = list_formal_trace_snapshots(
        project,
        property_name="top.p_req_ack",
        trace_kind="counterexample",
    )
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["trace_id"] == record["trace_id"]
    assert snapshot["signal_count"] == 2
    assert snapshot["step_count"] == 3
    assert snapshot["summary"]["partial_signal_steps"] == 1
    assert snapshot["metadata"]["engine"] == "unit"

    signals = list_formal_trace_signals(project, record["trace_id"])
    assert [item["name"] for item in signals] == ["top.clk", "top.req"]
    assert signals[0]["width"] == 1
    assert signals[0]["metadata"]["scope"] == "top"

    steps = list_formal_trace_steps(
        project,
        record["trace_id"],
        limit=1,
        offset=1,
    )
    assert len(steps) == 1
    assert steps[0]["step_position"] == 1
    assert steps[0]["step_index"] == 2
    assert steps[0]["time"] == 5
    assert steps[0]["cycle"] == 1
    assert steps[0]["values"] == {"top.clk": "1", "top.req": "1"}


def test_vcd_ingestion_persists_queryable_trace_snapshot(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    vcd = project.root / "trace.vcd"
    _write_vcd(vcd)

    record = ingest_formal_vcd_trace(
        project,
        vcd,
        property_name="top.c_bad",
        property_kind="cover",
        source="sby:cover",
    )

    snapshots = list_formal_trace_snapshots(project, trace_kind="witness")
    assert [item["trace_id"] for item in snapshots] == [record["trace_id"]]
    assert snapshots[0]["property_name"] == "top.c_bad"
    assert snapshots[0]["source"] == "sby:cover"

    steps = list_formal_trace_steps(project, record["trace_id"])
    assert len(steps) == 2
    assert steps[-1]["values"]["top.bad"] == "1"


def test_automatic_formal_trace_links_result_property_to_trace_snapshot(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "formal" / "runs" / "run-1"
    trace = run_dir / "artifacts" / "failure.vcd"
    _write_vcd(trace)

    payload = {
        "backend": "sby",
        "engine": "smtbmc",
        "request": {
            "mode": "bmc",
            "depth": 12,
            "properties": [],
        },
        "command": ["sby"],
        "returncode": 2,
        "status": "FAIL",
        "run_dir": str(run_dir),
        "log_path": str(run_dir / "formal.log"),
        "properties": [
            {
                "name": "top.p_failure",
                "kind": "assert",
                "status": "FAIL",
                "trace_path": "artifacts/failure.vcd",
            }
        ],
    }
    source = project.root / "formal-result.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = analyze_formal_result_file(project, source)

    normalization = result["properties"][0]["trace"]["normalization"]
    assert normalization["status"] == "NORMALIZED"
    assert len(normalization["trace_id"]) == 32

    properties = list_formal_property_results(project, result["snapshot_id"])
    assert len(properties) == 1
    assert properties[0]["trace_id"] == normalization["trace_id"]

    snapshots = list_formal_trace_snapshots(
        project,
        property_name="top.p_failure",
    )
    assert len(snapshots) == 1
    assert snapshots[0]["trace_id"] == normalization["trace_id"]


def test_formal_trace_query_validation(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    with pytest.raises(ValueError, match="Unsupported formal trace kind"):
        list_formal_trace_snapshots(project, trace_kind="proof")
    with pytest.raises(ValueError, match="limit must be >= 1"):
        list_formal_trace_steps(project, "missing", limit=0)
    with pytest.raises(ValueError, match="offset must be >= 0"):
        list_formal_trace_steps(project, "missing", offset=-1)

from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.formal.results import analyze_formal_result_file
from zddv.storage import (
    list_formal_property_results,
    list_formal_result_snapshots,
)


def _payload() -> dict:
    return {
        "backend": "example",
        "engine": "example-formal 1.0",
        "request": {
            "mode": "bmc",
            "depth": 20,
            "properties": [],
            "timeout_s": 30.0,
        },
        "command": ["example-formal", "--mode", "bmc"],
        "returncode": 0,
        "status": "FAIL",
        "run_dir": ".zddv/formal/runs/run-1",
        "log_path": ".zddv/formal/runs/run-1/formal.log",
        "runtime_ms": 12.5,
        "properties": [
            {
                "name": "p_safe",
                "kind": "assert",
                "status": "PASS",
            },
            {
                "name": "p_failure",
                "kind": "assert",
                "status": "FAIL",
                "depth": 7,
                "trace_path": "artifacts/p_failure.vcd",
            },
            {
                "name": "c_reachable",
                "kind": "cover",
                "status": "COVERED",
                "depth": 12,
                "trace_path": "artifacts/c_reachable.vcd",
            },
        ],
        "artifacts": ["artifacts/summary.json"],
    }


def test_formal_analysis_persists_snapshot_and_property_rows(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "formal.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")

    record = analyze_formal_result_file(project, source)

    snapshots = list_formal_result_snapshots(project, limit=10)
    assert len(snapshots) == 1
    row = snapshots[0]
    assert row["snapshot_id"] == record["snapshot_id"]
    assert row["status"] == "FAIL"
    assert row["mode"] == "bmc"
    assert row["proof_scope"] == "BOUNDED"
    assert row["property_count"] == 3
    assert row["counterexample_count"] == 1
    assert row["bounded_safe_count"] == 1
    assert row["proved_count"] == 0
    assert row["artifacts"] == ["artifacts/summary.json"]

    properties = list_formal_property_results(project, record["snapshot_id"])
    assert [item["name"] for item in properties] == [
        "p_safe",
        "p_failure",
        "c_reachable",
    ]
    assert properties[0]["interpretation"] == "BOUNDED_SAFE"
    assert properties[0]["effective_depth"] == 20
    assert properties[1]["interpretation"] == "COUNTEREXAMPLE"
    assert properties[1]["trace_role"] == "COUNTEREXAMPLE"
    assert properties[2]["interpretation"] == "COVERED"
    assert properties[2]["trace_role"] == "WITNESS"


def test_formal_snapshot_filters_preserve_mode_and_status(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "formal.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")
    analyze_formal_result_file(project, source)

    assert len(
        list_formal_result_snapshots(
            project,
            status="FAIL",
            mode="bmc",
        )
    ) == 1
    assert (
        list_formal_result_snapshots(
            project,
            status="PASS",
            mode="bmc",
        )
        == []
    )


def test_formal_cli_analyze_and_history(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "formal.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-analyze",
            str(source),
        ]
    )
    assert rc == 1
    analyze_output = capsys.readouterr().out
    assert "FORMAL FAIL" in analyze_output
    assert "mode=bmc scope=BOUNDED" in analyze_output
    assert "counterexamples=1" in analyze_output

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-history",
            "--status",
            "FAIL",
            "--mode",
            "bmc",
        ]
    )
    assert rc == 0
    history_output = capsys.readouterr().out
    assert "BOUNDED" in history_output
    assert "example" in history_output


def test_formal_persistence_auto_normalizes_attached_vcd_trace(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "artifacts" / "p_failure.vcd"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text(
        """$timescale 1 ns $end
$scope module top $end
$var wire 1 ! clk $end
$var wire 2 \" state [1:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b00 \"
#5
1!
b01 \"
""",
        encoding="utf-8",
    )

    payload = _payload()
    source = project.root / "formal.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    record = analyze_formal_result_file(project, source)

    counterexample = record["properties"][1]["trace"]
    assert counterexample["role"] == "COUNTEREXAMPLE"
    assert counterexample["normalization"]["status"] == "NORMALIZED"
    assert counterexample["normalization"]["signals"] == 2
    assert counterexample["normalization"]["steps"] == 2

    normalized_path = Path(counterexample["normalization"]["path"])
    assert normalized_path.is_file()
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    assert normalized["property"] == "p_failure"
    assert normalized["property_kind"] == "assert"
    assert normalized["trace_kind"] == "counterexample"
    assert normalized["source"] == "example-auto-vcd"
    assert normalized["input_path"] == str(trace.resolve())

    missing_witness = record["properties"][2]["trace"]
    assert missing_witness["role"] == "WITNESS"
    assert missing_witness["normalization"] == {
        "status": "MISSING",
        "path": "artifacts/c_reachable.vcd",
    }

    saved = json.loads(Path(record["report_path"]).read_text(encoding="utf-8"))
    assert saved["properties"][1]["trace"]["normalization"]["status"] == "NORMALIZED"
    assert saved["properties"][2]["trace"]["normalization"]["status"] == "MISSING"

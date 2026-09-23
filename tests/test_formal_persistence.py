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


def test_formal_analysis_auto_normalizes_semantic_vcd_trace(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "formal" / "runs" / "run-1"
    trace_path = run_dir / "artifacts" / "p_failure.vcd"
    _write_vcd(trace_path)

    source = project.root / "formal.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")

    record = analyze_formal_result_file(project, source)

    failed = next(item for item in record["properties"] if item["name"] == "p_failure")
    trace = failed["trace"]
    assert trace["resolved_path"] == str(trace_path.resolve())
    normalization = trace["normalization"]
    assert normalization["status"] == "NORMALIZED"

    normalized_path = Path(normalization["path"])
    assert normalized_path.is_file()
    saved = json.loads(normalized_path.read_text(encoding="utf-8"))
    assert saved["property"] == "p_failure"
    assert saved["property_kind"] == "assert"
    assert saved["trace_kind"] == "counterexample"
    assert saved["source"] == "example:example-formal 1.0"
    assert saved["summary"]["signals"] == 2
    assert saved["summary"]["steps"] == 2

    covered = next(item for item in record["properties"] if item["name"] == "c_reachable")
    assert covered["trace"]["normalization"]["status"] == "MISSING"


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

    limited = list_formal_property_results(
        project,
        record["snapshot_id"],
        limit=2,
    )
    assert [item["name"] for item in limited] == ["p_safe", "p_failure"]


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

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.formal.results import (
    analyze_formal_result_file,
    formal_result_from_data,
    formal_result_to_record,
    persist_formal_result,
)


def _payload(*, mode: str = "bmc", depth: int | None = 20) -> dict[str, object]:
    request: dict[str, object] = {
        "mode": mode,
        "properties": [],
        "timeout_s": 30.0,
    }
    if depth is not None:
        request["depth"] = depth

    return {
        "backend": "example",
        "engine": "example-formal 1.0",
        "request": request,
        "command": ["example-formal", "--mode", mode],
        "returncode": 0,
        "status": "FAIL",
        "run_dir": ".zddv/formal/runs/run-1",
        "log_path": ".zddv/formal/runs/run-1/formal.log",
        "runtime_ms": 12.5,
        "properties": [
            {
                "name": "p_bounded_safe",
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
            {
                "name": "c_missing",
                "kind": "cover",
                "status": "UNCOVERED",
            },
        ],
        "artifacts": ["artifacts/summary.json"],
    }




def _write_vcd(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """$timescale 1 ns $end
$scope module top $end
$var wire 1 ! req $end
$var wire 1 " ack $end
$upscope $end
$enddefinitions $end
#0
0!
0"
#5
1!
#10
1"
""",
        encoding="utf-8",
    )


def test_normalizes_bounded_results_and_trace_roles():
    result = formal_result_from_data(_payload())
    record = formal_result_to_record(result)

    assert record["request"]["mode"] == "bmc"
    assert record["request"]["scope"] == "BOUNDED"
    assert record["summary"] == {
        "properties": 4,
        "assertions": 2,
        "covers": 2,
        "property_statuses": {
            "COVERED": 1,
            "FAIL": 1,
            "PASS": 1,
            "UNCOVERED": 1,
        },
        "interpretations": {
            "BOUNDED_SAFE": 1,
            "COUNTEREXAMPLE": 1,
            "COVERED": 1,
            "UNREACHED": 1,
        },
        "counterexamples": 1,
        "bounded_safe_assertions": 1,
        "proved_assertions": 0,
        "covered_goals": 1,
        "unreached_goals": 1,
    }

    bounded = record["properties"][0]
    assert bounded["interpretation"] == "BOUNDED_SAFE"
    assert bounded["depth"] is None
    assert bounded["effective_depth"] == 20

    failed = record["properties"][1]
    assert failed["interpretation"] == "COUNTEREXAMPLE"
    assert failed["trace"] == {
        "path": "artifacts/p_failure.vcd",
        "role": "COUNTEREXAMPLE",
    }

    covered = record["properties"][2]
    assert covered["trace"] == {
        "path": "artifacts/c_reachable.vcd",
        "role": "WITNESS",
    }


def test_prove_pass_is_distinguished_from_bounded_safe():
    payload = _payload(mode="prove", depth=None)
    payload["status"] = "PASS"
    payload["properties"] = [
        {
            "name": "p_complete",
            "kind": "assert",
            "status": "PASS",
        }
    ]

    record = formal_result_to_record(formal_result_from_data(payload))

    assert record["request"]["scope"] == "UNBOUNDED"
    assert record["properties"][0]["interpretation"] == "PROVED"
    assert record["summary"]["proved_assertions"] == 1
    assert record["summary"]["bounded_safe_assertions"] == 0


def test_bmc_pass_without_known_depth_is_not_labeled_bounded_safe():
    payload = _payload(mode="bmc", depth=None)
    payload["status"] = "PASS"
    payload["properties"] = [
        {
            "name": "p_unscoped",
            "kind": "assert",
            "status": "PASS",
        }
    ]

    record = formal_result_to_record(formal_result_from_data(payload))

    assert record["properties"][0]["interpretation"] == "PASS_BOUNDED_UNSCOPED"
    assert record["summary"]["bounded_safe_assertions"] == 0


def test_cover_miss_stays_cover_evidence():
    payload = _payload(mode="cover", depth=50)
    payload["status"] = "PASS"
    payload["properties"] = [
        {
            "name": "c_not_reached",
            "kind": "cover",
            "status": "UNCOVERED",
        }
    ]

    record = formal_result_to_record(formal_result_from_data(payload))

    assert record["request"]["scope"] == "COVER"
    assert record["properties"][0]["interpretation"] == "UNREACHED"
    assert record["summary"]["unreached_goals"] == 1
    assert record["status"] == "PASS"


def test_rejects_non_normalized_cover_status():
    payload = _payload(mode="cover", depth=10)
    payload["properties"] = [
        {
            "name": "c_bad",
            "kind": "cover",
            "status": "FAIL",
        }
    ]

    with pytest.raises(ValueError, match="cover property status"):
        formal_result_from_data(payload)


def test_rejects_non_array_command():
    payload = _payload()
    payload["command"] = "example-formal"

    with pytest.raises(ValueError, match="command must be an array"):
        formal_result_from_data(payload)


def test_analyze_formal_result_file_writes_evidence_record(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    input_path = project.root / "formal-result.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")

    record = analyze_formal_result_file(project, input_path)

    report_path = Path(record["report_path"])
    assert report_path == project.root / ".zddv" / "formal" / "latest.json"
    assert report_path.is_file()

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["snapshot_id"] == record["snapshot_id"]
    assert persisted["project"] == project.name
    assert persisted["input_path"] == str(input_path.resolve())
    assert persisted["summary"]["counterexamples"] == 1

def test_persist_formal_result_auto_normalizes_backend_vcd_trace(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "formal" / "sby" / "run-1"
    trace_path = run_dir / "engine_0" / "trace.vcd"
    _write_vcd(trace_path)

    payload = _payload()
    payload["backend"] = "sby"
    payload["run_dir"] = str(run_dir)
    payload["log_path"] = str(run_dir / "formal.log")
    payload["properties"] = [
        {
            "name": "top.p_req_ack",
            "kind": "assert",
            "status": "FAIL",
            "depth": 7,
            "trace_path": "engine_0/trace.vcd",
        }
    ]

    result = formal_result_from_data(payload)
    record = persist_formal_result(
        project,
        result,
        input_path=result.log_path,
        output=run_dir / "zddv-formal-result.json",
    )

    normalization = record["trace_normalization"]
    assert normalization["summary"] == {
        "reported_traces": 1,
        "normalized": 1,
        "missing": 0,
        "errors": 0,
        "skipped": 0,
    }
    trace = normalization["traces"][0]
    assert trace["role"] == "COUNTEREXAMPLE"
    assert trace["status"] == "NORMALIZED"
    assert trace["resolved_path"] == str(trace_path.resolve())
    assert trace["input_sha256"]

    normalized_path = Path(trace["normalized_path"])
    assert normalized_path == run_dir / "normalized-traces" / "0000-counterexample.json"
    saved = json.loads(normalized_path.read_text(encoding="utf-8"))
    assert saved["property"] == "top.p_req_ack"
    assert saved["trace_kind"] == "counterexample"
    assert saved["source"] == "sby"
    assert saved["summary"]["steps"] == 3


def test_persist_formal_result_retains_malformed_vcd_without_aborting(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "formal" / "sby" / "run-2"
    trace_path = run_dir / "engine_0" / "bad.vcd"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("$timescale 1 ns $end\n#0\n0!\n", encoding="utf-8")

    payload = _payload()
    payload["backend"] = "sby"
    payload["run_dir"] = str(run_dir)
    payload["log_path"] = str(run_dir / "formal.log")
    payload["properties"] = [
        {
            "name": "top.p_bad",
            "kind": "assert",
            "status": "FAIL",
            "depth": 3,
            "trace_path": "engine_0/bad.vcd",
        }
    ]

    result = formal_result_from_data(payload)
    record = persist_formal_result(
        project,
        result,
        input_path=result.log_path,
        output=run_dir / "zddv-formal-result.json",
    )

    normalization = record["trace_normalization"]
    assert normalization["summary"]["normalized"] == 0
    assert normalization["summary"]["errors"] == 1
    trace = normalization["traces"][0]
    assert trace["status"] == "ERROR"
    assert "VCD header is incomplete" in trace["reason"]
    assert Path(record["report_path"]).is_file()


def test_persist_formal_result_skips_non_vcd_trace_semantics(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "formal" / "sby" / "run-3"

    payload = _payload()
    payload["backend"] = "sby"
    payload["run_dir"] = str(run_dir)
    payload["log_path"] = str(run_dir / "formal.log")
    payload["properties"] = [
        {
            "name": "top.c_seen",
            "kind": "cover",
            "status": "COVERED",
            "depth": 5,
            "trace_path": "engine_0/trace.fsdb",
        }
    ]

    result = formal_result_from_data(payload)
    record = persist_formal_result(
        project,
        result,
        input_path=result.log_path,
        output=run_dir / "zddv-formal-result.json",
    )

    normalization = record["trace_normalization"]
    assert normalization["summary"]["skipped"] == 1
    trace = normalization["traces"][0]
    assert trace["role"] == "WITNESS"
    assert trace["status"] == "SKIPPED"
    assert "supports VCD only" in trace["reason"]


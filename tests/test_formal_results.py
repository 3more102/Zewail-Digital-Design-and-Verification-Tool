from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.formal.results import (
    analyze_formal_result_file,
    formal_result_from_data,
    formal_result_to_record,
)
from zddv.storage import (
    list_formal_property_results,
    list_formal_snapshots,
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

    snapshots = list_formal_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == record["snapshot_id"]
    assert snapshots[0]["backend"] == "example"
    assert snapshots[0]["status"] == "FAIL"
    assert snapshots[0]["mode"] == "bmc"
    assert snapshots[0]["depth"] == 20
    assert snapshots[0]["property_count"] == 4
    assert snapshots[0]["counterexample_count"] == 1
    assert snapshots[0]["command"] == ["example-formal", "--mode", "bmc"]

    properties = list_formal_property_results(project, record["snapshot_id"])
    assert len(properties) == 4
    assert properties[0]["interpretation"] == "BOUNDED_SAFE"
    assert properties[0]["effective_depth"] == 20
    assert properties[1]["interpretation"] == "COUNTEREXAMPLE"
    assert properties[1]["trace_role"] == "COUNTEREXAMPLE"
    assert properties[2]["trace_role"] == "WITNESS"

    assert list_formal_snapshots(project, status="PASS") == []
    assert len(list_formal_snapshots(project, status="FAIL", mode="bmc")) == 1


def test_formal_import_and_history_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    input_path = project.root / "formal-result.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-import",
            str(input_path),
        ]
    )
    assert rc == 0
    imported = capsys.readouterr().out
    assert "FORMAL: status=FAIL mode=bmc backend=example properties=4" in imported
    assert "Counterexamples: 1" in imported
    assert "Snapshot:" in imported

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-history",
            "--status",
            "FAIL",
            "--mode",
            "bmc",
            "--backend",
            "example",
        ]
    )
    assert rc == 0
    history = capsys.readouterr().out
    assert "STATUS" in history
    assert "example" in history
    assert "FAIL" in history
    assert "bmc" in history
    assert " 1 " in history or "    1" in history

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-history",
            "--status",
            "PASS",
        ]
    )
    assert rc == 0
    assert "No formal snapshots found." in capsys.readouterr().out

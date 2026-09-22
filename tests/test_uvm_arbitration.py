from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_arbitration_requests,
    list_uvm_arbitration_snapshots,
    record_run,
)
from zddv.uvm_arbitration import (
    analyze_uvm_arbitration_file,
    parse_uvm_arbitration_data,
)


def _contender(
    request_id: str,
    sequence_id: str,
    sequence: str,
    *,
    item_id: str | None = None,
    priority: int | None = None,
    request_order: int | None = None,
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "sequence_id": sequence_id,
        "sequence": sequence,
        "item_id": item_id,
        "priority": priority,
        "request_order": request_order,
    }


def _decision(
    decision_id: str,
    granted_request_id: str,
    contenders: list[dict[str, object]],
    *,
    time: str | None = None,
    mode: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "decision_id": decision_id,
        "sequencer": "uvm_test_top.env.seqr",
        "granted_request_id": granted_request_id,
        "time": time,
        "contenders": contenders,
    }
    if mode is not None:
        result["mode"] = mode
    return result


def _fair_trace() -> dict[str, object]:
    req_a = _contender("req-a", "seq-a", "producer_a", item_id="item-a", priority=100)
    req_b = _contender("req-b", "seq-b", "producer_b", item_id="item-b", priority=100)
    req_c = _contender("req-c", "seq-c", "producer_c", item_id="item-c", priority=50)
    return {
        "source": "unit-test",
        "fairness_bound": 2,
        "decisions": [
            _decision("d0", "req-a", [req_a, req_b], time="10 ns"),
            _decision("d1", "req-c", [req_b, req_c], time="20 ns"),
            _decision("d2", "req-b", [req_b], time="30 ns"),
        ],
    }


def test_parses_observed_arbitration_and_fairness_metrics():
    result = parse_uvm_arbitration_data(_fair_trace())

    assert result["status"] == "PASS"
    assert result["source"] == "unit-test"
    assert result["fairness_bound"] == 2
    assert result["summary"] == {
        "decisions": 3,
        "requests": 3,
        "grants": 3,
        "pending": 0,
        "violations": 0,
        "fairness_violations": 0,
        "policy_violations": 0,
        "policy_checked_decisions": 0,
        "policy_partial_decisions": 0,
        "policy_observational_decisions": 3,
        "max_wait_decisions": 2,
    }
    assert result["mode"] == "UNSPECIFIED"
    req_b = next(item for item in result["requests"] if item["request_id"] == "req-b")
    assert req_b["exposure_count"] == 3
    assert req_b["lost_decisions"] == 2
    assert req_b["grant_decision_id"] == "d2"
    assert result["grant_counts_by_sequence"] == {
        "producer_a": 1,
        "producer_b": 1,
        "producer_c": 1,
    }


def test_fairness_bound_is_explicit_and_can_fail():
    payload = _fair_trace()
    payload["fairness_bound"] = 1
    result = parse_uvm_arbitration_data(payload)

    assert result["status"] == "FAIL"
    assert result["summary"]["fairness_violations"] == 1
    assert any(
        violation["code"] == "FAIRNESS_BOUND_EXCEEDED"
        and violation["request_id"] == "req-b"
        for violation in result["violations"]
    )


def test_detects_grant_not_in_contenders_and_request_identity_change():
    req = _contender("req-a", "seq-a", "producer_a", item_id="item-a", priority=10)
    changed = _contender("req-a", "seq-a", "producer_a", item_id="item-a", priority=20)
    result = parse_uvm_arbitration_data(
        {
            "decisions": [
                _decision("d0", "missing", [req]),
                _decision("d1", "req-a", [changed]),
            ]
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert "GRANT_NOT_A_CONTENDER" in codes
    assert "REQUEST_IDENTITY_CHANGED" in codes
    assert result["status"] == "FAIL"


def test_fifo_policy_uses_explicit_request_order():
    first = _contender(
        "req-a",
        "seq-a",
        "producer_a",
        priority=100,
        request_order=4,
    )
    second = _contender(
        "req-b",
        "seq-b",
        "producer_b",
        priority=100,
        request_order=5,
    )
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "decisions": [_decision("d0", "req-b", [first, second])],
        }
    )

    assert result["status"] == "FAIL"
    assert result["summary"]["policy_violations"] == 1
    check = result["policy_checks"][0]
    assert check["check_status"] == "CHECKED"
    assert check["expected_request_ids"] == ["req-a"]
    assert check["earliest_request_order"] == 4
    assert any(
        item["code"] == "FIFO_ORDER_VIOLATION"
        for item in result["violations"]
    )


def test_fifo_policy_without_request_order_is_partial_not_guessed():
    first = _contender("req-a", "seq-a", "producer_a", priority=100)
    second = _contender("req-b", "seq-b", "producer_b", priority=100)
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "decisions": [_decision("d0", "req-b", [first, second])],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["policy_violations"] == 0
    assert result["summary"]["policy_partial_decisions"] == 1
    assert result["policy_checks"][0]["missing_evidence"] == ["request_order"]


def test_strict_fifo_checks_priority_then_fifo_tie_order():
    low = _contender(
        "req-low",
        "seq-low",
        "low_seq",
        priority=100,
        request_order=0,
    )
    high_first = _contender(
        "req-high-a",
        "seq-high-a",
        "high_a",
        priority=300,
        request_order=1,
    )
    high_second = _contender(
        "req-high-b",
        "seq-high-b",
        "high_b",
        priority=300,
        request_order=2,
    )
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_STRICT_FIFO",
            "decisions": [
                _decision(
                    "d0",
                    "req-high-b",
                    [low, high_first, high_second],
                )
            ],
        }
    )

    assert result["status"] == "FAIL"
    check = result["policy_checks"][0]
    assert check["check_status"] == "CHECKED"
    assert check["highest_priority"] == 300
    assert check["eligible_request_ids"] == ["req-high-a", "req-high-b"]
    assert check["expected_request_ids"] == ["req-high-a"]
    assert "STRICT_FIFO_ORDER_VIOLATION" in check["violation_codes"]


def test_strict_random_checks_only_highest_priority_eligibility():
    high_a = _contender("req-a", "seq-a", "a", priority=200)
    high_b = _contender("req-b", "seq-b", "b", priority=200)
    low = _contender("req-low", "seq-low", "low", priority=100)

    passing = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_STRICT_RANDOM",
            "decisions": [_decision("d0", "req-b", [high_a, high_b, low])],
        }
    )
    assert passing["status"] == "PASS"
    assert passing["policy_checks"][0]["check_status"] == "CHECKED"
    assert passing["policy_checks"][0]["expected_request_ids"] == ["req-a", "req-b"]

    failing = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_STRICT_RANDOM",
            "decisions": [_decision("d0", "req-low", [high_a, high_b, low])],
        }
    )
    assert failing["status"] == "FAIL"
    assert any(
        item["code"] == "STRICT_PRIORITY_VIOLATION"
        for item in failing["violations"]
    )


@pytest.mark.parametrize(
    "mode",
    [
        "UVM_SEQ_ARB_RANDOM",
        "UVM_SEQ_ARB_WEIGHTED",
        "UVM_SEQ_ARB_USER",
        "UNSPECIFIED",
    ],
)
def test_non_deterministic_or_unspecified_modes_remain_observational(mode: str):
    first = _contender(
        "req-a",
        "seq-a",
        "a",
        priority=500,
        request_order=0,
    )
    second = _contender(
        "req-b",
        "seq-b",
        "b",
        priority=1,
        request_order=10,
    )
    result = parse_uvm_arbitration_data(
        {
            "mode": mode,
            "decisions": [_decision("d0", "req-b", [first, second])],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["policy_observational_decisions"] == 1
    assert result["policy_checks"][0]["check_status"] == "OBSERVATIONAL"


def test_decision_mode_can_override_trace_default():
    first = _contender(
        "req-a",
        "seq-a",
        "a",
        priority=100,
        request_order=0,
    )
    second = _contender(
        "req-b",
        "seq-b",
        "b",
        priority=100,
        request_order=1,
    )
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "decisions": [
                _decision(
                    "d0",
                    "req-b",
                    [first, second],
                    mode="UVM_SEQ_ARB_RANDOM",
                )
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["mode"] == "UVM_SEQ_ARB_FIFO"
    assert result["decisions"][0]["mode"] == "UVM_SEQ_ARB_RANDOM"
    assert result["policy_checks"][0]["check_status"] == "OBSERVATIONAL"


def test_rejects_unknown_arbitration_mode_and_negative_request_order():
    with pytest.raises(ValueError, match="mode must be one of"):
        parse_uvm_arbitration_data({"mode": "MAGIC", "decisions": []})

    bad = _contender(
        "req-a",
        "seq-a",
        "a",
        priority=100,
        request_order=-1,
    )
    with pytest.raises(ValueError, match="request_order"):
        parse_uvm_arbitration_data(
            {
                "mode": "UVM_SEQ_ARB_FIFO",
                "decisions": [_decision("d0", "req-a", [bad])],
            }
        )


def _record_run(project, run_id: str) -> None:
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text("simulation complete\n", encoding="utf-8")
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": "2026-09-22T10:00:00+00:00",
            "project": project.name,
            "simulator": "questa",
            "simulator_version": "Questa test",
            "top": "tb_top",
            "test": "arb_case",
            "seed": 31,
            "status": "PASS",
            "returncode": 0,
            "duration_ms": 1.0,
            "run_dir": str(run_dir),
            "log": str(log),
            "waveform": None,
            "coverage": None,
            "timeout_s": 10.0,
            "command": ["vsim"],
            "plusargs": [],
        },
    )


def test_analyze_persists_arbitration_snapshot_and_requests(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "arbitration.json"
    trace.write_text(json.dumps(_fair_trace()), encoding="utf-8")

    result = analyze_uvm_arbitration_file(project, trace)

    assert Path(result["normalized_path"]).is_file()
    assert Path(result["report_path"]).is_file()
    snapshots = list_uvm_arbitration_snapshots(project, limit=10)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["decision_count"] == 3
    assert snapshots[0]["max_wait_decisions"] == 2

    requests = list_uvm_arbitration_requests(project, result["snapshot_id"])
    assert len(requests) == 3
    req_b = next(item for item in requests if item["request_id"] == "req-b")
    assert req_b["granted"] is True
    assert req_b["pending"] is False
    assert req_b["lost_decisions"] == 2


def test_cli_arbitration_analysis_history_and_run_correlation(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    _record_run(project, "run-arb")
    trace = project.root / "arbitration.json"
    trace.write_text(json.dumps(_fair_trace()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-arbitration-analyze",
            str(trace),
            "--run",
            "run-arb",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM ARBITRATION PASS" in output
    assert "max-wait=2" in output
    assert "Run: run-arb" in output

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-arbitration-history",
            "--run",
            "run-arb",
        ]
    )
    assert rc == 0
    history = capsys.readouterr().out
    assert "run-arb" in history
    assert "PASS" in history
    assert "2" in history


def test_cli_fairness_bound_override_can_fail(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "arbitration.json"
    trace.write_text(json.dumps(_fair_trace()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-arbitration-analyze",
            str(trace),
            "--fairness-bound",
            "1",
        ]
    )
    assert rc == 1
    output = capsys.readouterr().out
    assert "FAIRNESS_BOUND_EXCEEDED" in output

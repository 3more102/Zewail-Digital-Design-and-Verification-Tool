from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_arbitration_requests,
    list_uvm_arbitration_snapshots,
    record_run,
)
from zddv.uvm_arbitration import (
    analyze_uvm_arbitration_file,
    analyze_uvm_arbitration_log,
    parse_uvm_arbitration_data,
    parse_uvm_arbitration_log_text,
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
        "max_wait_decisions": 2,
    }
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


def test_explicit_fifo_policy_checks_request_order():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "decisions": [
                _decision(
                    "fifo-bad",
                    "req-late",
                    [
                        _contender(
                            "req-early",
                            "seq-a",
                            "producer_a",
                            request_order=0,
                        ),
                        _contender(
                            "req-late",
                            "seq-b",
                            "producer_b",
                            request_order=1,
                        ),
                    ],
                )
            ],
        }
    )

    assert result["status"] == "FAIL"
    assert result["arbitration_mode"] == "UVM_SEQ_ARB_FIFO"
    assert result["decisions"][0]["policy_check"]["checked"] is True
    assert result["decisions"][0]["policy_check"]["expected_request_id"] == "req-early"
    assert any(
        violation["code"] == "FIFO_ORDER_MISMATCH"
        for violation in result["violations"]
    )


def test_strict_modes_use_explicit_priority_and_fifo_tie_evidence():
    strict_random = parse_uvm_arbitration_data(
        {
            "decisions": [
                _decision(
                    "strict-random",
                    "req-low",
                    [
                        _contender(
                            "req-low",
                            "seq-low",
                            "low_seq",
                            priority=100,
                        ),
                        _contender(
                            "req-high",
                            "seq-high",
                            "high_seq",
                            priority=300,
                        ),
                    ],
                    mode="UVM_SEQ_ARB_STRICT_RANDOM",
                )
            ]
        }
    )
    assert strict_random["status"] == "FAIL"
    assert any(
        violation["code"] == "STRICT_PRIORITY_MISMATCH"
        for violation in strict_random["violations"]
    )

    strict_fifo = parse_uvm_arbitration_data(
        {
            "decisions": [
                _decision(
                    "strict-fifo",
                    "req-second",
                    [
                        _contender(
                            "req-first",
                            "seq-a",
                            "producer_a",
                            priority=300,
                            request_order=0,
                        ),
                        _contender(
                            "req-second",
                            "seq-b",
                            "producer_b",
                            priority=300,
                            request_order=1,
                        ),
                    ],
                    mode="UVM_SEQ_ARB_STRICT_FIFO",
                )
            ]
        }
    )
    assert strict_fifo["status"] == "FAIL"
    assert any(
        violation["code"] == "STRICT_FIFO_ORDER_MISMATCH"
        for violation in strict_fifo["violations"]
    )


def test_policy_checks_are_skipped_when_required_evidence_is_absent():
    fifo = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "decisions": [
                _decision(
                    "fifo-observational",
                    "req-b",
                    [
                        _contender("req-a", "seq-a", "producer_a"),
                        _contender("req-b", "seq-b", "producer_b"),
                    ],
                )
            ],
        }
    )
    assert fifo["status"] == "PASS"
    assert fifo["decisions"][0]["policy_check"]["checked"] is False

    random_mode = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_RANDOM",
            "decisions": [
                _decision(
                    "random-observational",
                    "req-b",
                    [
                        _contender(
                            "req-a",
                            "seq-a",
                            "producer_a",
                            priority=100,
                            request_order=0,
                        ),
                        _contender(
                            "req-b",
                            "seq-b",
                            "producer_b",
                            priority=1,
                            request_order=1,
                        ),
                    ],
                )
            ],
        }
    )
    assert random_mode["status"] == "PASS"
    assert random_mode["decisions"][0]["policy_check"]["checked"] is False


def test_rejects_unknown_explicit_arbitration_mode():
    try:
        parse_uvm_arbitration_data(
            {
                "mode": "NOT_A_UVM_MODE",
                "decisions": [],
            }
        )
    except ValueError as exc:
        assert "mode must be one of" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid arbitration mode")



def _marker_line(decision: dict[str, object]) -> str:
    return "ZDDV_UVM_ARBITRATION " + json.dumps(decision)


def test_parse_arbitration_log_markers_preserves_actual_line_provenance():
    decision = _decision(
        "d0",
        "req-a",
        [
            _contender(
                "req-a",
                "seq-a",
                "producer_a",
                priority=100,
                request_order=0,
            )
        ],
        mode="UVM_SEQ_ARB_FIFO",
    )
    decision["metadata"] = {"log_line": 999, "tag": "from-helper"}
    text = "\n".join(
        [
            "ordinary simulator text",
            "UVM_INFO prefix " + _marker_line(decision),
            "ordinary trailer",
        ]
    )

    result = parse_uvm_arbitration_log_text(text)

    assert result["status"] == "PASS"
    assert result["input_mode"] == "explicit-log-marker"
    assert result["marker"] == "ZDDV_UVM_ARBITRATION"
    assert result["marker_lines"] == [2]
    assert result["decisions"][0]["metadata"]["log_line"] == 2
    assert result["decisions"][0]["metadata"]["tag"] == "from-helper"


def test_parse_arbitration_log_rejects_missing_and_invalid_markers():
    try:
        parse_uvm_arbitration_log_text("ordinary simulator text")
    except ValueError as exc:
        assert "No ZDDV_UVM_ARBITRATION markers found" in str(exc)
    else:
        raise AssertionError("Expected ValueError when no arbitration marker exists")

    try:
        parse_uvm_arbitration_log_text("ZDDV_UVM_ARBITRATION {not-json}")
    except ValueError as exc:
        assert "has invalid JSON" in str(exc)
    else:
        raise AssertionError("Expected ValueError for malformed arbitration marker JSON")

def _record_run(
    project,
    run_id: str,
    *,
    log_text: str = "simulation complete\
",
) -> None:
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text(log_text, encoding="utf-8")
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

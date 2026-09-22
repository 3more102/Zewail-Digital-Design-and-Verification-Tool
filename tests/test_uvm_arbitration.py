from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.uvm_arbitration import (
    analyze_uvm_arbitration_file,
    parse_uvm_arbitration_data,
)


def _candidate(
    sequence_id: str,
    *,
    priority: int | None = None,
    request_order: int | None = None,
) -> dict[str, object]:
    return {
        "sequence_id": sequence_id,
        "sequence": f"{sequence_id}_seq",
        "priority": priority,
        "request_order": request_order,
    }


def _round(
    round_id: str,
    winner: str,
    contenders: list[dict[str, object]],
    *,
    mode: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "round_id": round_id,
        "sequencer": "uvm_test_top.env.seqr",
        "winner_sequence_id": winner,
        "contenders": contenders,
    }
    if mode is not None:
        result["mode"] = mode
    return result


def test_reconstructs_round_robin_fairness_evidence():
    result = parse_uvm_arbitration_data(
        {
            "source": "unit-test",
            "mode": "UVM_SEQ_ARB_RANDOM",
            "rounds": [
                _round(
                    "r0",
                    "a",
                    [_candidate("a"), _candidate("b")],
                ),
                _round(
                    "r1",
                    "b",
                    [_candidate("a"), _candidate("b")],
                ),
                _round(
                    "r2",
                    "a",
                    [_candidate("a"), _candidate("b")],
                ),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["source"] == "unit-test"
    assert result["summary"] == {
        "rounds": 3,
        "sequencers": 1,
        "sequences": 2,
        "contended_rounds": 3,
        "uncontended_rounds": 0,
        "violations": 0,
        "observed_max_wait_rounds": 1,
        "fairness_failures": 0,
    }
    metrics = {
        item["sequence_id"]: item
        for item in result["fairness"]["sequences"]
    }
    assert metrics["a"]["grants"] == 2
    assert metrics["a"]["max_wait_rounds"] == 1
    assert metrics["b"]["grants"] == 1
    assert metrics["b"]["max_wait_rounds"] == 1


def test_checks_fifo_and_strict_priority_when_evidence_is_explicit():
    result = parse_uvm_arbitration_data(
        {
            "rounds": [
                _round(
                    "fifo",
                    "late",
                    [
                        _candidate("early", request_order=0),
                        _candidate("late", request_order=1),
                    ],
                    mode="UVM_SEQ_ARB_FIFO",
                ),
                _round(
                    "strict",
                    "low",
                    [
                        _candidate("low", priority=100),
                        _candidate("high", priority=300),
                    ],
                    mode="UVM_SEQ_ARB_STRICT_RANDOM",
                ),
                _round(
                    "strict-fifo",
                    "second",
                    [
                        _candidate(
                            "first",
                            priority=300,
                            request_order=0,
                        ),
                        _candidate(
                            "second",
                            priority=300,
                            request_order=1,
                        ),
                        _candidate(
                            "low2",
                            priority=100,
                            request_order=0,
                        ),
                    ],
                    mode="UVM_SEQ_ARB_STRICT_FIFO",
                ),
            ]
        }
    )

    assert result["status"] == "FAIL"
    assert [
        item["code"]
        for item in result["violations"]
    ] == [
        "FIFO_ORDER_MISMATCH",
        "STRICT_PRIORITY_MISMATCH",
        "STRICT_FIFO_ORDER_MISMATCH",
    ]


def test_fairness_bound_is_explicit_and_reports_once_per_sequence():
    result = parse_uvm_arbitration_data(
        {
            "fairness_max_wait_rounds": 1,
            "rounds": [
                _round(
                    "r0",
                    "a",
                    [_candidate("a"), _candidate("b")],
                ),
                _round(
                    "r1",
                    "a",
                    [_candidate("a"), _candidate("b")],
                ),
                _round(
                    "r2",
                    "a",
                    [_candidate("a"), _candidate("b")],
                ),
            ],
        }
    )

    assert result["status"] == "FAIL"
    fairness = [
        item
        for item in result["violations"]
        if item["code"] == "FAIRNESS_WAIT_EXCEEDED"
    ]
    assert len(fairness) == 1
    assert fairness[0]["sequence_id"] == "b"
    metrics = {
        item["sequence_id"]: item
        for item in result["fairness"]["sequences"]
    }
    assert metrics["b"]["max_wait_rounds"] == 3
    assert result["summary"]["fairness_failures"] == 1


def test_wait_streak_resets_when_sequence_is_not_eligible():
    result = parse_uvm_arbitration_data(
        {
            "rounds": [
                _round(
                    "r0",
                    "a",
                    [_candidate("a"), _candidate("b")],
                ),
                _round(
                    "r1",
                    "a",
                    [_candidate("a")],
                ),
                _round(
                    "r2",
                    "a",
                    [_candidate("a"), _candidate("b")],
                ),
            ]
        }
    )

    metrics = {
        item["sequence_id"]: item
        for item in result["fairness"]["sequences"]
    }
    assert metrics["b"]["eligible_rounds"] == 2
    assert metrics["b"]["max_wait_rounds"] == 1
    assert metrics["b"]["current_wait_rounds"] == 1


def test_detects_invalid_winner_duplicate_contender_and_round_id():
    result = parse_uvm_arbitration_data(
        {
            "rounds": [
                _round(
                    "same",
                    "missing",
                    [_candidate("a"), _candidate("a")],
                ),
                _round(
                    "same",
                    "b",
                    [_candidate("b")],
                ),
            ]
        }
    )

    assert [
        item["code"]
        for item in result["violations"]
    ] == [
        "DUPLICATE_CONTENDER",
        "WINNER_NOT_CONTENDER",
        "DUPLICATE_ROUND_ID",
    ]


def test_rejects_unknown_mode_and_invalid_fairness_bound():
    try:
        parse_uvm_arbitration_data(
            {
                "mode": "NOT_UVM",
                "rounds": [],
            }
        )
    except ValueError as exc:
        assert "mode must be one of" in str(exc)
    else:
        raise AssertionError(
            "Expected ValueError for unknown arbitration mode"
        )

    try:
        parse_uvm_arbitration_data(
            {
                "fairness_max_wait_rounds": -1,
                "rounds": [],
            }
        )
    except ValueError as exc:
        assert "fairness_max_wait_rounds" in str(exc)
    else:
        raise AssertionError(
            "Expected ValueError for invalid fairness bound"
        )


def test_analyze_writes_snapshot_and_latest_report(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "arbitration.json"
    trace.write_text(
        json.dumps(
            {
                "mode": "UVM_SEQ_ARB_FIFO",
                "rounds": [
                    _round(
                        "r0",
                        "a",
                        [
                            _candidate("a", request_order=0),
                            _candidate("b", request_order=1),
                        ],
                    )
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_uvm_arbitration_file(
        project,
        trace,
        source="uvm-helper",
    )

    assert result["status"] == "PASS"
    assert result["source"] == "uvm-helper"
    assert Path(result["normalized_path"]).is_file()
    assert Path(result["report_path"]).is_file()
    payload = json.loads(
        Path(result["report_path"]).read_text(encoding="utf-8")
    )
    assert payload["analysis"] == "uvm_sequencer_arbitration"
    assert payload["summary"]["rounds"] == 1
    assert payload["fairness"]["observed_max_wait_rounds"] == 1


def test_cli_analyzes_arbitration_trace(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "arbitration.json"
    trace.write_text(
        json.dumps(
            {
                "mode": "UVM_SEQ_ARB_STRICT_FIFO",
                "fairness_max_wait_rounds": 2,
                "rounds": [
                    _round(
                        "r0",
                        "b",
                        [
                            _candidate(
                                "a",
                                priority=100,
                                request_order=0,
                            ),
                            _candidate(
                                "b",
                                priority=300,
                                request_order=1,
                            ),
                        ],
                    ),
                    _round(
                        "r1",
                        "b",
                        [
                            _candidate(
                                "a",
                                priority=100,
                                request_order=0,
                            ),
                            _candidate(
                                "b",
                                priority=300,
                                request_order=1,
                            ),
                        ],
                    ),
                    _round(
                        "r2",
                        "a",
                        [
                            _candidate(
                                "a",
                                priority=100,
                                request_order=0,
                            )
                        ],
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-arbitration-analyze",
            str(trace),
            "--source",
            "cli-test",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM ARBITRATION PASS" in output
    assert "observed-max-wait=2" in output
    latest = (
        project.root
        / ".zddv"
        / "uvm"
        / "arbitration"
        / "latest.json"
    )
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["source"] == "cli-test"
    assert payload["summary"]["violations"] == 0

from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_arbitration_candidates,
    list_uvm_arbitration_snapshots,
    record_run,
)
from zddv.uvm_arbitration import analyze_uvm_arbitration_file, parse_uvm_arbitration_data


def _candidate(
    sequence_id: str,
    *,
    order: int | None = None,
    priority: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "sequence_id": sequence_id,
        "sequence": f"{sequence_id}_seq",
    }
    if order is not None:
        payload["request_order"] = order
    if priority is not None:
        payload["priority"] = priority
    return payload


def _round(
    round_id: str,
    winner: str,
    candidates: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "round_id": round_id,
        "sequencer": "uvm_test_top.env.seqr",
        "winner_sequence_id": winner,
        "candidates": candidates,
    }


def test_fifo_and_strict_modes_validate_deterministic_rules():
    fifo = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "rounds": [
                _round(
                    "r0",
                    "a",
                    [_candidate("a", order=1), _candidate("b", order=2)],
                )
            ],
        }
    )
    assert fifo["status"] == "PASS"

    bad_fifo = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "rounds": [
                _round(
                    "r0",
                    "b",
                    [_candidate("a", order=1), _candidate("b", order=2)],
                )
            ],
        }
    )
    assert bad_fifo["status"] == "FAIL"
    assert bad_fifo["violations"][0]["code"] == "FIFO_ORDER_VIOLATION"

    strict_fifo = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_STRICT_FIFO",
            "rounds": [
                _round(
                    "r0",
                    "b",
                    [
                        _candidate("a", order=1, priority=100),
                        _candidate("b", order=2, priority=200),
                        _candidate("c", order=3, priority=200),
                    ],
                )
            ],
        }
    )
    assert strict_fifo["status"] == "PASS"

    strict_random = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_STRICT_RANDOM",
            "rounds": [
                _round(
                    "r0",
                    "a",
                    [
                        _candidate("a", priority=100),
                        _candidate("b", priority=200),
                    ],
                )
            ],
        }
    )
    assert strict_random["status"] == "FAIL"
    assert strict_random["violations"][0]["code"] == "STRICT_RANDOM_PRIORITY_VIOLATION"


def test_random_weighted_and_user_modes_do_not_invent_randomness_failures():
    for mode in (
        "UVM_SEQ_ARB_RANDOM",
        "UVM_SEQ_ARB_WEIGHTED",
        "UVM_SEQ_ARB_USER",
    ):
        result = parse_uvm_arbitration_data(
            {
                "mode": mode,
                "rounds": [
                    _round(
                        "r0",
                        "b",
                        [_candidate("a"), _candidate("b")],
                    )
                ],
            }
        )
        assert result["status"] == "PASS"
        assert result["summary"]["violations"] == 0


def test_structural_evidence_and_wait_metrics():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_RANDOM",
            "rounds": [
                _round("r0", "a", [_candidate("a"), _candidate("b")]),
                _round("r1", "a", [_candidate("a"), _candidate("b")]),
                _round("r2", "b", [_candidate("a"), _candidate("b")]),
            ],
        }
    )
    by_id = {item["sequence_id"]: item for item in result["sequence_metrics"]}
    assert by_id["a"]["wins"] == 2
    assert by_id["a"]["max_wait_rounds"] == 1
    assert by_id["b"]["wins"] == 1
    assert by_id["b"]["max_wait_rounds"] == 2
    assert result["summary"]["max_wait_rounds"] == 2

    missing_winner = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_USER",
            "rounds": [_round("r0", "missing", [_candidate("a")])],
        }
    )
    assert missing_winner["status"] == "FAIL"
    assert missing_winner["violations"][0]["code"] == "WINNER_NOT_ELIGIBLE"


def test_fifo_requires_unambiguous_request_order_evidence():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "rounds": [
                _round("r0", "a", [_candidate("a"), _candidate("b", order=1)]),
                _round(
                    "r1",
                    "a",
                    [_candidate("a", order=1), _candidate("b", order=1)],
                ),
            ],
        }
    )
    codes = [violation["code"] for violation in result["violations"]]
    assert "MISSING_REQUEST_ORDER" in codes
    assert "AMBIGUOUS_REQUEST_ORDER" in codes


def test_analysis_persists_snapshot_and_candidate_evidence(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "arbitration.json"
    trace.write_text(
        json.dumps(
            {
                "source": "unit-test",
                "mode": "UVM_SEQ_ARB_STRICT_FIFO",
                "rounds": [
                    _round(
                        "r0",
                        "b",
                        [
                            _candidate("a", order=1, priority=100),
                            _candidate("b", order=2, priority=200),
                        ],
                    )
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_uvm_arbitration_file(project, trace)
    assert result["status"] == "PASS"
    assert Path(result["normalized_path"]).is_file()
    assert Path(result["report_path"]).is_file()

    snapshots = list_uvm_arbitration_snapshots(project, limit=10)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["mode"] == "UVM_SEQ_ARB_STRICT_FIFO"

    candidates = list_uvm_arbitration_candidates(project, result["snapshot_id"])
    assert len(candidates) == 2
    assert candidates[1]["winner"] == 1
    assert candidates[1]["priority"] == 200


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
            "test": "arbitration_case",
            "seed": 41,
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


def test_cli_analysis_history_and_run_correlation(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    _record_run(project, "run-arb")
    trace = project.root / "arbitration.json"
    trace.write_text(
        json.dumps(
            {
                "mode": "UVM_SEQ_ARB_FIFO",
                "rounds": [
                    _round(
                        "r0",
                        "a",
                        [_candidate("a", order=1), _candidate("b", order=2)],
                    )
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
            "--run",
            "run-arb",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM ARBITRATION PASS" in output
    assert "Mode: UVM_SEQ_ARB_FIFO" in output
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
    assert "UVM_SEQ_ARB_FIFO" in history
    assert "PASS" in history

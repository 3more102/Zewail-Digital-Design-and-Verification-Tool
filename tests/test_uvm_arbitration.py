from __future__ import annotations

import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_arbitration_decisions,
    list_uvm_arbitration_events,
    list_uvm_arbitration_snapshots,
    list_uvm_arbitration_violations,
    record_run,
)
from zddv.uvm_arbitration import (
    analyze_uvm_arbitration_file,
    parse_uvm_arbitration_data,
)


def _event(
    request_id: str,
    event: str,
    *,
    sequence_id: str,
    priority: int = 100,
    time: str | None = None,
    sequencer: str = "uvm_test_top.env.seqr",
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "event": event,
        "sequence_id": sequence_id,
        "sequencer": sequencer,
        "priority": priority,
        "time": time,
    }


def test_fifo_grants_requests_in_queue_order():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a"),
                _event("r2", "REQUEST", sequence_id="seq-b"),
                _event("r1", "GRANT", sequence_id="seq-a"),
                _event("r2", "GRANT", sequence_id="seq-b"),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["requests"] == 2
    assert result["summary"]["grants"] == 2
    assert result["summary"]["pending"] == 0
    assert result["decisions"][0]["expected_request_id"] == "r1"


def test_fifo_detects_out_of_order_grant():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a"),
                _event("r2", "REQUEST", sequence_id="seq-b"),
                _event("r2", "GRANT", sequence_id="seq-b"),
            ],
        }
    )

    assert result["status"] == "FAIL"
    assert [item["code"] for item in result["violations"]] == [
        "FIFO_ORDER_VIOLATION"
    ]


def test_strict_fifo_prefers_highest_priority_then_fifo():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_STRICT_FIFO",
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a", priority=100),
                _event("r2", "REQUEST", sequence_id="seq-b", priority=200),
                _event("r3", "REQUEST", sequence_id="seq-c", priority=200),
                _event("r2", "GRANT", sequence_id="seq-b", priority=200),
                _event("r3", "GRANT", sequence_id="seq-c", priority=200),
                _event("r1", "GRANT", sequence_id="seq-a", priority=100),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["decisions"][0]["expected_request_id"] == "r2"
    assert result["decisions"][0]["highest_priority"] == 200


def test_strict_random_rejects_lower_priority_choice():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_STRICT_RANDOM",
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a", priority=100),
                _event("r2", "REQUEST", sequence_id="seq-b", priority=300),
                _event("r1", "GRANT", sequence_id="seq-a", priority=100),
            ],
        }
    )

    assert result["status"] == "FAIL"
    assert result["violations"][0]["code"] == "STRICT_PRIORITY_VIOLATION"
    assert result["decisions"][0]["eligible_request_ids"] == ["r2"]


def test_random_mode_is_observational_without_fairness_policy():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_RANDOM",
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a"),
                _event("r2", "REQUEST", sequence_id="seq-b"),
                _event("r2", "GRANT", sequence_id="seq-b"),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["selection_policy"] == "observational"
    assert result["summary"]["max_bypass_observed"] == 1
    assert result["summary"]["pending"] == 1


def test_arbitration_queues_and_bypass_are_scoped_per_sequencer():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_FIFO",
            "max_bypass": 0,
            "events": [
                _event("a1", "REQUEST", sequence_id="seq-a", sequencer="env.seqr_a"),
                _event("b1", "REQUEST", sequence_id="seq-b", sequencer="env.seqr_b"),
                _event("b1", "GRANT", sequence_id="seq-b", sequencer="env.seqr_b"),
                _event("a1", "GRANT", sequence_id="seq-a", sequencer="env.seqr_a"),
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["max_bypass_observed"] == 0
    assert result["decisions"][0]["sequencer"] == "env.seqr_b"
    assert result["decisions"][0]["pending_request_ids"] == ["b1"]
    assert result["decisions"][1]["pending_request_ids"] == ["a1"]


def test_optional_fairness_policy_flags_excessive_bypass():
    result = parse_uvm_arbitration_data(
        {
            "mode": "UVM_SEQ_ARB_RANDOM",
            "max_bypass": 1,
            "events": [
                _event("r1", "REQUEST", sequence_id="seq-a"),
                _event("r2", "REQUEST", sequence_id="seq-b"),
                _event("r3", "REQUEST", sequence_id="seq-c"),
                _event("r2", "GRANT", sequence_id="seq-b"),
                _event("r3", "GRANT", sequence_id="seq-c"),
            ],
        }
    )

    assert result["status"] == "FAIL"
    assert result["summary"]["max_bypass_observed"] == 2
    assert [item["code"] for item in result["violations"]] == [
        "FAIRNESS_BYPASS_LIMIT"
    ]
    assert result["violations"][0]["request_id"] == "r1"


def test_grant_without_request_and_identity_change_are_reported():
    result = parse_uvm_arbitration_data(
        {
            "events": [
                _event("missing", "GRANT", sequence_id="seq-x"),
                _event("r1", "REQUEST", sequence_id="seq-a"),
                _event("r1", "GRANT", sequence_id="seq-b"),
            ],
        }
    )

    assert result["status"] == "FAIL"
    assert [item["code"] for item in result["violations"]] == [
        "GRANT_WITHOUT_REQUEST",
        "IDENTITY_CHANGED",
    ]


def test_analyze_writes_snapshot_and_latest_report(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "uvm-arbitration.json"
    trace.write_text(
        json.dumps(
            {
                "source": "uvm-helper",
                "mode": "UVM_SEQ_ARB_FIFO",
                "events": [
                    _event("r1", "REQUEST", sequence_id="seq-a", time="1 ns"),
                    _event("r1", "GRANT", sequence_id="seq-a", time="2 ns"),
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_uvm_arbitration_file(project, trace)

    assert result["status"] == "PASS"
    assert Path(result["normalized_path"]).is_file()
    assert Path(result["report_path"]).is_file()
    payload = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert payload["analysis"] == "uvm_arbitration"
    assert payload["summary"]["grants"] == 1

    snapshots = list_uvm_arbitration_snapshots(project, limit=10)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["mode"] == "UVM_SEQ_ARB_FIFO"
    assert snapshots[0]["request_count"] == 1
    assert snapshots[0]["grant_count"] == 1

    events = list_uvm_arbitration_events(project, result["snapshot_id"])
    assert [item["event"] for item in events] == ["REQUEST", "GRANT"]
    decisions = list_uvm_arbitration_decisions(project, result["snapshot_id"])
    assert decisions[0]["expected_request_id"] == "r1"
    assert decisions[0]["eligible_request_ids"] == ["r1"]
    assert decisions[0]["sequencer"] == "uvm_test_top.env.seqr"
    assert list_uvm_arbitration_violations(project, result["snapshot_id"]) == []

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


def test_cli_arbitration_analysis_history_and_run_correlation(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    _record_run(project, "run-arb")
    trace = project.root / "uvm-arbitration.json"
    trace.write_text(
        json.dumps(
            {
                "source": "uvm-helper",
                "mode": "UVM_SEQ_ARB_STRICT_FIFO",
                "events": [
                    _event(
                        "r1",
                        "REQUEST",
                        sequence_id="seq-a",
                        priority=100,
                        time="1 ns",
                    ),
                    _event(
                        "r2",
                        "REQUEST",
                        sequence_id="seq-b",
                        priority=200,
                        time="2 ns",
                    ),
                    _event(
                        "r2",
                        "GRANT",
                        sequence_id="seq-b",
                        priority=200,
                        time="3 ns",
                    ),
                    _event(
                        "r1",
                        "GRANT",
                        sequence_id="seq-a",
                        priority=100,
                        time="4 ns",
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
            "uvm-arb-analyze",
            str(trace),
            "--run",
            "run-arb",
            "--max-bypass",
            "2",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM ARBITRATION PASS" in output
    assert "UVM_SEQ_ARB_STRICT_FIFO" in output
    assert "Run: run-arb" in output

    rows = list_uvm_arbitration_snapshots(
        project,
        limit=10,
        run_id="run-arb",
        mode="UVM_SEQ_ARB_STRICT_FIFO",
    )
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-arb"
    assert rows[0]["max_bypass_limit"] == 2

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-arbitration-history",
            "--run",
            "run-arb",
            "--mode",
            "UVM_SEQ_ARB_STRICT_FIFO",
        ]
    )
    assert rc == 0
    history = capsys.readouterr().out
    assert "run-arb" in history
    assert "UVM_SEQ_ARB_STRICT_FIFO" in history
    assert "PASS" in history


def test_cli_arbitration_failure_returns_nonzero_and_persists_violation(
    tmp_path: Path,
    capsys,
):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "uvm-arbitration-fail.json"
    trace.write_text(
        json.dumps(
            {
                "mode": "UVM_SEQ_ARB_FIFO",
                "events": [
                    _event("r1", "REQUEST", sequence_id="seq-a"),
                    _event("r2", "REQUEST", sequence_id="seq-b"),
                    _event("r2", "GRANT", sequence_id="seq-b"),
                ],
            }
        ),
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-arb-analyze",
            str(trace),
        ]
    )
    assert rc == 1
    output = capsys.readouterr().out
    assert "FIFO_ORDER_VIOLATION" in output

    rows = list_uvm_arbitration_snapshots(
        project,
        limit=10,
        status="FAIL",
    )
    assert len(rows) == 1
    violations = list_uvm_arbitration_violations(
        project,
        rows[0]["snapshot_id"],
    )
    assert [item["code"] for item in violations] == ["FIFO_ORDER_VIOLATION"]


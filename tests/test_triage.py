from pathlib import Path

from zddv.triage import (
    build_triage_report,
    failure_signature,
    group_failure_records,
    normalize_failure_line,
    write_triage_report,
)


def test_normalize_failure_line_masks_run_specific_values():
    a = normalize_failure_line(
        "%Error: tb.sv:91: Assertion failed at cycle 120 seed 7 expected 12 got 13"
    )
    b = normalize_failure_line(
        "%Error: tb.sv:104: Assertion failed at cycle 443 seed 99 expected 44 got 45"
    )
    assert a == b


def test_failure_signature_groups_same_failure_across_seeds(tmp_path: Path):
    log_a = tmp_path / "a.log"
    log_b = tmp_path / "b.log"
    log_a.write_text(
        "%Error: tb.sv:91: Assertion failed at cycle 120 seed 7 expected 12 got 13\n",
        encoding="utf-8",
    )
    log_b.write_text(
        "%Error: tb.sv:104: Assertion failed at cycle 443 seed 99 expected 44 got 45\n",
        encoding="utf-8",
    )

    assert failure_signature(log_a, "FAIL") == failure_signature(log_b, "FAIL")


def test_timeout_signature_is_explicit(tmp_path: Path):
    assert failure_signature(tmp_path / "missing.log", "TIMEOUT") == "TIMEOUT"


def test_group_failure_records_separates_distinct_failures(tmp_path: Path):
    log1 = tmp_path / "one.log"
    log2 = tmp_path / "two.log"
    log3 = tmp_path / "three.log"
    log1.write_text("FATAL: scoreboard mismatch expected 12 got 13", encoding="utf-8")
    log2.write_text("FATAL: scoreboard mismatch expected 44 got 45", encoding="utf-8")
    log3.write_text("ERROR: protocol response missing", encoding="utf-8")

    records = [
        {"run_id": "r1", "test_name": "a", "seed": 1, "status": "FAIL", "log_path": str(log1)},
        {"run_id": "r2", "test_name": "a", "seed": 2, "status": "FAIL", "log_path": str(log2)},
        {"run_id": "r3", "test_name": "b", "seed": 3, "status": "FAIL", "log_path": str(log3)},
    ]
    groups = group_failure_records(records)
    assert len(groups) == 2
    assert [group["count"] for group in groups] == [2, 1]


def test_write_triage_report(tmp_path: Path):
    log = tmp_path / "failure.log"
    log.write_text("UVM_ERROR scoreboard mismatch expected 3 got 5", encoding="utf-8")
    records = [
        {"run_id": "r1", "test_name": "smoke", "seed": 1, "status": "FAIL", "log_path": str(log)},
        {"run_id": "r2", "test_name": "smoke", "seed": 2, "status": "PASS", "log_path": str(log)},
    ]

    report = build_triage_report(records)
    assert report["runs"] == 1
    assert report["groups"] == 1

    output = write_triage_report(records, tmp_path / "triage.json")
    assert output.exists()
    assert '"groups": 1' in output.read_text(encoding="utf-8")

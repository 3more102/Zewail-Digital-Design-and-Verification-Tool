from pathlib import Path

from zddv.triage import failure_signature, group_failure_records, normalize_failure_line


def test_normalize_failure_line_masks_volatile_numbers():
    a = normalize_failure_line("%Error: tb.sv:91: Assertion failed at cycle 120 seed 7")
    b = normalize_failure_line("%Error: tb.sv:104: Assertion failed at cycle 443 seed 99")
    assert a == b


def test_failure_signature_groups_same_failure_across_seeds(tmp_path: Path):
    log_a = tmp_path / "a.log"
    log_b = tmp_path / "b.log"
    log_a.write_text("%Error: tb.sv:91: Assertion failed at cycle 120 seed 7\n", encoding="utf-8")
    log_b.write_text("%Error: tb.sv:104: Assertion failed at cycle 443 seed 99\n", encoding="utf-8")

    sig_a = failure_signature(log_a, "FAIL")
    sig_b = failure_signature(log_b, "FAIL")
    assert sig_a == sig_b


def test_timeout_signature_is_explicit(tmp_path: Path):
    assert failure_signature(tmp_path / "missing.log", "TIMEOUT") == "TIMEOUT"


def test_group_failure_records_preserves_distinct_failures(tmp_path: Path):
    log1 = tmp_path / "one.log"
    log2 = tmp_path / "two.log"
    log3 = tmp_path / "three.log"
    log1.write_text("FATAL: scoreboard mismatch expected 12 got 13", encoding="utf-8")
    log2.write_text("FATAL: scoreboard mismatch expected 44 got 45", encoding="utf-8")
    log3.write_text("ERROR: protocol response missing", encoding="utf-8")

    records = [
        {"run_id": "r1", "test": "a", "seed": 1, "status": "FAIL", "log": str(log1)},
        {"run_id": "r2", "test": "a", "seed": 2, "status": "FAIL", "log": str(log2)},
        {"run_id": "r3", "test": "b", "seed": 3, "status": "FAIL", "log": str(log3)},
    ]
    groups = group_failure_records(records)
    assert len(groups) == 2
    assert sorted(group["count"] for group in groups) == [1, 2]

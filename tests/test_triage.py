from pathlib import Path

from zddv.triage import group_failure_records, signature_from_text


def test_signature_normalizes_volatile_values():
    a = signature_from_text(
        "ASSERTION FAILED at /tmp/run-1/tb.sv:42 expected=0x10 got=17",
        status="FAIL",
    )
    b = signature_from_text(
        "ASSERTION FAILED at /tmp/run-2/tb.sv:99 expected=0x20 got=31",
        status="FAIL",
    )

    assert a == b
    assert "0x#" in a
    assert "<path>" in a


def test_timeout_has_stable_signature():
    assert signature_from_text("anything", status="TIMEOUT") == "TIMEOUT"


def test_group_failure_records(tmp_path: Path):
    log1 = tmp_path / "a.log"
    log2 = tmp_path / "b.log"
    log3 = tmp_path / "c.log"

    log1.write_text("FAIL mismatch expected 10 got 11\n", encoding="utf-8")
    log2.write_text("FAIL mismatch expected 20 got 21\n", encoding="utf-8")
    log3.write_text("fatal protocol violation on channel 3\n", encoding="utf-8")

    records = [
        {
            "run_id": "r1",
            "created_at": "2026-09-21T20:00:00+00:00",
            "test_name": "smoke",
            "seed": 1,
            "status": "FAIL",
            "log_path": str(log1),
        },
        {
            "run_id": "r2",
            "created_at": "2026-09-21T20:01:00+00:00",
            "test_name": "smoke",
            "seed": 2,
            "status": "FAIL",
            "log_path": str(log2),
        },
        {
            "run_id": "r3",
            "created_at": "2026-09-21T20:02:00+00:00",
            "test_name": "corner",
            "seed": 3,
            "status": "FAIL",
            "log_path": str(log3),
        },
    ]

    groups = group_failure_records(records)

    assert len(groups) == 2
    assert groups[0]["count"] == 2
    assert groups[0]["tests"] == ["smoke"]
    assert groups[0]["seeds"] == [1, 2]

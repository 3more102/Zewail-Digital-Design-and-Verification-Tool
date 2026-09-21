import json
from pathlib import Path

import pytest

from zddv.cdc import analyze_async_fifo_file, analyze_async_fifo_trace
from zddv.cli import main
from zddv.config import initialize_project


def _gray(value: int) -> int:
    return value ^ (value >> 1)


def test_async_fifo_dynamic_invariants_pass_with_blocking_and_wrap():
    result = analyze_async_fifo_trace(
        {
            "source": "unit-test",
            "pointer_width": 3,
            "write_events": [
                {
                    "cycle": 0,
                    "reset": 1,
                    "binary_before": 0,
                    "binary_after": 0,
                    "gray_before": 0,
                    "gray_after": 0,
                },
                {
                    "cycle": 1,
                    "request": 1,
                    "full": 0,
                    "accepted": 1,
                    "binary_before": 0,
                    "binary_after": 1,
                    "gray_before": _gray(0),
                    "gray_after": _gray(1),
                },
                {
                    "cycle": 2,
                    "request": 1,
                    "full": 1,
                    "accepted": 0,
                    "binary_before": 1,
                    "binary_after": 1,
                    "gray_before": _gray(1),
                    "gray_after": _gray(1),
                },
            ],
            "read_events": [
                {
                    "cycle": 10,
                    "request": 1,
                    "empty": 0,
                    "accepted": 1,
                    "binary_before": 7,
                    "binary_after": 0,
                    "gray_before": _gray(7),
                    "gray_after": _gray(0),
                }
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["accepted_writes"] == 1
    assert result["summary"]["accepted_reads"] == 1
    assert result["summary"]["blocked_write_requests"] == 1
    assert result["summary"]["reset_events"] == 1
    assert result["summary"]["violations"] == 0
    assert result["scope"]["not_static_cdc_signoff"] is True


def test_async_fifo_reports_acceptance_pointer_and_gray_errors():
    result = analyze_async_fifo_trace(
        {
            "pointer_width": 4,
            "write_events": [
                {
                    "cycle": 4,
                    "request": 1,
                    "full": 1,
                    "accepted": 1,
                    "binary_before": 0,
                    "binary_after": 2,
                    "gray_before": 0,
                    "gray_after": _gray(2),
                }
            ],
            "read_events": [],
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "acceptance_mismatch" in codes
    assert "binary_pointer_increment_error" in codes
    assert "gray_transition_error" in codes


def test_async_fifo_reports_encoding_and_trace_continuity_errors():
    result = analyze_async_fifo_trace(
        {
            "pointer_width": 4,
            "write_events": [
                {
                    "cycle": 0,
                    "request": 1,
                    "full": 0,
                    "accepted": 1,
                    "binary_before": 0,
                    "binary_after": 1,
                    "gray_before": 0,
                    "gray_after": 7,
                },
                {
                    "cycle": 1,
                    "request": 0,
                    "full": 0,
                    "accepted": 0,
                    "binary_before": 3,
                    "binary_after": 3,
                    "gray_before": _gray(3),
                    "gray_after": _gray(3),
                },
            ],
            "read_events": [],
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert "gray_encoding_mismatch" in codes
    assert "binary_trace_discontinuity" in codes
    assert "gray_trace_discontinuity" in codes


def test_async_fifo_schema_rejects_missing_local_flow_control():
    with pytest.raises(ValueError, match="full"):
        analyze_async_fifo_trace(
            {
                "pointer_width": 3,
                "write_events": [
                    {
                        "request": 1,
                        "accepted": 1,
                        "binary_before": 0,
                        "binary_after": 1,
                        "gray_before": 0,
                        "gray_after": 1,
                    }
                ],
            }
        )


def test_async_fifo_file_and_cli_write_report(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "fifo_cdc.json"
    trace.write_text(
        json.dumps(
            {
                "source": "file-test",
                "pointer_width": 4,
                "write_events": [
                    {
                        "cycle": 1,
                        "request": 1,
                        "full": 0,
                        "accepted": 1,
                        "binary_before": 0,
                        "binary_after": 1,
                        "gray_before": 0,
                        "gray_after": 1,
                    }
                ],
                "read_events": [
                    {
                        "cycle": 3,
                        "request": 1,
                        "empty": 1,
                        "accepted": 0,
                        "binary_before": 0,
                        "binary_after": 0,
                        "gray_before": 0,
                        "gray_after": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_async_fifo_file(project, trace)
    report_path = Path(result["report_path"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["analysis"] == "async_fifo_cdc_dynamic"
    assert report["summary"]["violations"] == 0

    rc = main(
        [
            "--project",
            str(project.root),
            "async-fifo-analyze",
            str(trace),
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "ASYNC FIFO CDC PASS" in output
    assert "2 event(s)" in output

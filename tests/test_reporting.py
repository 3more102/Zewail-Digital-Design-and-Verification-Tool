from pathlib import Path
from xml.etree import ElementTree as ET

from zddv.reporting import write_html_report, write_junit_report


def test_write_junit_report(tmp_path: Path):
    records = [
        {
            "run_id": "run-pass",
            "project": "demo",
            "simulator": "verilator",
            "test_name": "smoke",
            "seed": 1,
            "status": "PASS",
            "returncode": 0,
            "duration_ms": 10.0,
            "log_path": "/tmp/pass.log",
        },
        {
            "run_id": "run-fail",
            "project": "demo",
            "simulator": "verilator",
            "test_name": "corner",
            "seed": 2,
            "status": "FAIL",
            "returncode": 1,
            "duration_ms": 20.0,
            "log_path": "/tmp/fail.log",
        },
        {
            "run_id": "run-timeout",
            "project": "demo",
            "simulator": "verilator",
            "test_name": "stress",
            "seed": 3,
            "status": "TIMEOUT",
            "returncode": 124,
            "duration_ms": 30.0,
            "log_path": "/tmp/timeout.log",
        },
    ]

    output = write_junit_report(records, tmp_path / "junit.xml", suite_name="demo")
    root = ET.parse(output).getroot()

    assert root.tag == "testsuite"
    assert root.attrib["tests"] == "3"
    assert root.attrib["failures"] == "1"
    assert root.attrib["errors"] == "1"

    cases = root.findall("testcase")
    assert len(cases) == 3
    assert cases[0].find("failure") is None
    assert cases[1].find("failure") is not None
    assert cases[2].find("error") is not None



def test_write_html_report_escapes_and_summarizes(tmp_path: Path):
    records = [
        {
            "run_id": "run-pass",
            "created_at": "2026-09-21T19:00:00+00:00",
            "simulator": "verilator",
            "test_name": "smoke<unsafe>",
            "seed": 1,
            "status": "PASS",
            "duration_ms": 10.0,
        },
        {
            "run_id": "run-fail",
            "created_at": "2026-09-21T19:01:00+00:00",
            "simulator": "verilator",
            "test_name": "corner",
            "seed": 2,
            "status": "FAIL",
            "duration_ms": 20.0,
        },
    ]
    groups = [
        {
            "count": 1,
            "statuses": ["FAIL"],
            "tests": ["corner"],
            "seeds": [2],
            "signature": "ASSERT x < y",
        }
    ]

    output = write_html_report(
        records,
        groups,
        tmp_path / "report.html",
        suite_name="demo",
    )
    html = output.read_text(encoding="utf-8")

    assert "ZDDV Verification Report" in html
    assert "50.0%" in html
    assert "smoke&lt;unsafe&gt;" in html
    assert "ASSERT x &lt; y" in html
    assert "run-fail" in html

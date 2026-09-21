from pathlib import Path
from xml.etree import ElementTree as ET

from zddv.reporting import write_junit_report


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

import xml.etree.ElementTree as ET
from pathlib import Path

from zddv.regression import load_regression, write_junit_report


def test_load_regression(tmp_path: Path):
    path = tmp_path / "regression.toml"
    path.write_text(
        """
[regression]
name = "smoke"
jobs = 2

[[tests]]
name = "basic"
seeds = [1, 2, 3]
timeout_s = 5
plusargs = ["+FOO=1"]
""".strip(),
        encoding="utf-8",
    )

    spec = load_regression(path)

    assert spec.name == "smoke"
    assert spec.jobs == 2
    assert len(spec.cases) == 1
    assert spec.cases[0].name == "basic"
    assert spec.cases[0].seeds == (1, 2, 3)
    assert spec.cases[0].plusargs == ("+FOO=1",)
    assert spec.cases[0].timeout_s == 5.0


def test_write_junit_report(tmp_path: Path):
    record = {
        "regression": "smoke",
        "total": 3,
        "failed": 1,
        "timed_out": 1,
        "runs": [
            {
                "test": "ok",
                "seed": 1,
                "status": "PASS",
                "duration_ms": 10.0,
                "log": "ok.log",
            },
            {
                "test": "bad",
                "seed": 2,
                "status": "FAIL",
                "duration_ms": 20.0,
                "log": "bad.log",
                "failure_signature": "FAIL:abc:oops",
            },
            {
                "test": "slow",
                "seed": 3,
                "status": "TIMEOUT",
                "duration_ms": 30.0,
                "log": "slow.log",
                "failure_signature": "TIMEOUT",
            },
        ],
    }

    path = write_junit_report(record, tmp_path / "junit.xml")
    root = ET.parse(path).getroot()

    assert root.attrib["tests"] == "3"
    assert root.attrib["failures"] == "1"
    assert root.attrib["errors"] == "1"
    assert len(root.findall("testcase")) == 3
    assert len(root.findall("testcase/failure")) == 1
    assert len(root.findall("testcase/error")) == 1

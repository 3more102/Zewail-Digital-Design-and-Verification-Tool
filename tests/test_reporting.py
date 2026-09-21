from pathlib import Path
import json
import xml.etree.ElementTree as ET

from zddv.config import initialize_project
from zddv.reporting import export_runs
from zddv.storage import record_run


def _record(run_id: str, status: str, seed: int) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-21T19:20:0{seed}+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 100.0 * seed,
        "run_dir": f"/tmp/{run_id}",
        "log": f"/tmp/{run_id}/simulation.log",
        "waveform": None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def test_export_json(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _record("run-1", "PASS", 1))

    output = export_runs(project, tmp_path / "runs.json", format="json")
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["project"] == "demo"
    assert payload["runs"][0]["run_id"] == "run-1"


def test_export_junit_marks_failures(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _record("run-pass", "PASS", 1))
    record_run(project, _record("run-timeout", "TIMEOUT", 2))

    output = export_runs(project, tmp_path / "junit.xml", format="junit")
    root = ET.parse(output).getroot()

    assert root.tag == "testsuite"
    assert root.attrib["tests"] == "2"
    assert root.attrib["failures"] == "1"
    cases = root.findall("testcase")
    assert len(cases) == 2
    timeout_case = next(
        case for case in cases if "seed=2" in case.attrib["name"]
    )
    failure = timeout_case.find("failure")
    assert failure is not None
    assert failure.attrib["type"] == "timeout"

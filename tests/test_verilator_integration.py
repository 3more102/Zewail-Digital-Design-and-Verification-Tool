from pathlib import Path
import shutil

import pytest

from zddv.config import load_project
from zddv.coverage import merge_verilator_coverage
from zddv.simulator import VerilatorBackend
from zddv.storage import list_assertion_events, list_coverage_snapshots


@pytest.mark.skipif(shutil.which("verilator") is None, reason="Verilator not installed")
def test_counter_build_and_run():
    root = Path(__file__).parents[1] / "examples" / "counter"
    project = load_project(root)
    backend = VerilatorBackend()

    build = backend.build(project)
    assert build.passed, build.log_path.read_text(encoding="utf-8")

    run = backend.run(project)
    assert run.status == "PASS", run.log_path.read_text(encoding="utf-8")
    assert run.waveform_path is not None
    assert run.waveform_path.exists()
    assert run.coverage_path is not None
    assert run.coverage_path.exists()
    assert (run.run_dir / "run.json").exists()

    coverage = merge_verilator_coverage(project)
    assert Path(coverage["merged"]).exists()
    assert Path(coverage["summary"]).exists()
    assert Path(coverage["metrics_path"]).exists()
    assert coverage["metrics"]["total_points"] > 0
    assert 0.0 <= coverage["metrics"]["hit_rate"] <= 100.0

    snapshots = list_coverage_snapshots(project, limit=5)
    assert snapshots
    assert snapshots[0]["snapshot_id"] == coverage["snapshot_id"]
    assert snapshots[0]["total_points"] == coverage["metrics"]["total_points"]



@pytest.mark.skipif(shutil.which("verilator") is None, reason="Verilator not installed")
def test_assertion_failure_is_normalized_and_stored():
    root = Path(__file__).parents[1] / "examples" / "assertions"
    project = load_project(root)
    backend = VerilatorBackend()

    build = backend.build(project)
    assert build.passed, build.log_path.read_text(encoding="utf-8")

    run = backend.run(project)
    assert run.status == "FAIL", run.log_path.read_text(encoding="utf-8")
    assert run.returncode != 0
    assert run.assertion_count >= 1
    assert run.assertion_failures >= 1

    events = list_assertion_events(project, limit=20, run_id=run.run_id)
    assert events
    assert any(event["status"] == "FAIL" for event in events)
    assert any(event["parser"] == "verilator" for event in events)

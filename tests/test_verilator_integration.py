from pathlib import Path
import shutil

import pytest

from zddv.config import load_project
from zddv.coverage import merge_verilator_coverage
from zddv.simulator import VerilatorBackend


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

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.coverage import merge_xcelium_coverage, parse_xcelium_imc_summary
from zddv.storage import list_coverage_score_snapshots


IMC_SUMMARY = """IMC(64): test
Starting batch mode
Legend: Metric* means cumulative e.g. Block* means Cumulative Block Coverage
name Overall* Average Overall* Covered Code* Average Code* Covered Fsm* Average Fsm* Covered Functional* Average Functional* Covered
----------------------------------------------------------------------------------------------------------------------------------
tb 93.25% 91.00% (8/10) 88.00% 87.00% (4/5) n/a n/a n/a 100.00% 100.00% (4/4)
"""


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="xcelium",
        run_dir=".zddv/runs",
        coverage=True,
    )


def _coverage_run(project: ProjectConfig, run_id: str) -> Path:
    path = project.root / project.run_dir / run_id / "coverage" / run_id
    path.mkdir(parents=True)
    (path / f"icc_{run_id}.ucd").write_text("ucd fixture\n", encoding="utf-8")
    return path.resolve()


def test_parse_xcelium_imc_summary_normalizes_overall_score():
    metrics = parse_xcelium_imc_summary(IMC_SUMMARY)

    assert metrics["tool_total_coverage"] == pytest.approx(93.25)
    assert metrics["scope"] == "tb"
    assert metrics["source"] == "imc-summary"
    assert metrics["by_metric"] == {}
    assert metrics["by_metric_counts"] == {}


def test_parse_xcelium_imc_summary_requires_documented_header():
    with pytest.raises(ValueError, match="Overall coverage header"):
        parse_xcelium_imc_summary("IMC completed without a summary table\n")


def test_merge_xcelium_coverage_requires_imc(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr("zddv.coverage.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="Cadence IMC was not found"):
        merge_xcelium_coverage(project)


def test_merge_xcelium_coverage_requires_captured_runs(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    with pytest.raises(RuntimeError, match="No Xcelium coverage run databases"):
        merge_xcelium_coverage(project)


def test_merge_xcelium_coverage_persists_score_snapshot(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    run_a = _coverage_run(project, "run-a")
    run_b = _coverage_run(project, "run-b")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )
    captured: dict[str, object] = {}

    def fake_run(command, cwd):
        captured["command"] = list(command)
        captured["cwd"] = Path(cwd)
        merged = Path(cwd) / "merged"
        merged.mkdir(parents=True)
        (merged / "icc_merged.ucd").write_text("merged ucd\n", encoding="utf-8")
        (merged / "icc_merged.ucm").write_text("merged ucm\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=IMC_SUMMARY)

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_xcelium_coverage(project)

    assert result["metrics_status"] == "normalized"
    assert result["metrics"]["tool_total_coverage"] == pytest.approx(93.25)
    assert result["snapshot_id"] is not None
    assert result["inputs"] == [str(run_a), str(run_b)]

    command = captured["command"]
    assert command[0] == "/opt/cadence/bin/imc"
    assert command[1] == "-execcmd"
    assert "merge " in command[2]
    assert str(run_a) in command[2]
    assert str(run_b) in command[2]
    assert "-metrics all -initial_model union_all" in command[2]
    assert "load -run {merged}" in command[2]
    assert "report -summary -cumulative on -inst -local off" in command[2]

    history = list_coverage_score_snapshots(project)
    assert len(history) == 1
    assert history[0]["simulator"] == "xcelium"
    assert history[0]["score"] == pytest.approx(93.25)
    assert history[0]["input_count"] == 2


def test_merge_xcelium_coverage_retains_unparsed_evidence_without_score(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    def fake_run(command, cwd):
        merged = Path(cwd) / "merged"
        merged.mkdir(parents=True)
        (merged / "icc_merged.ucd").write_text("merged ucd\n", encoding="utf-8")
        (merged / "icc_merged.ucm").write_text("merged ucm\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="IMC merge completed\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_xcelium_coverage(project)

    assert result["metrics"] is None
    assert result["metrics_status"] == "summary-unparsed"
    assert result["snapshot_id"] is None
    assert Path(result["summary"]).read_text(encoding="utf-8") == "IMC merge completed\n"
    assert list_coverage_score_snapshots(project) == []


def test_merge_xcelium_coverage_requires_merged_ucd_and_ucm(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    def fake_run(command, cwd):
        merged = Path(cwd) / "merged"
        merged.mkdir(parents=True)
        (merged / "icc_merged.ucd").write_text("merged ucd\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=IMC_SUMMARY)

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    with pytest.raises(RuntimeError, match="IMC coverage merge/report failed"):
        merge_xcelium_coverage(project)

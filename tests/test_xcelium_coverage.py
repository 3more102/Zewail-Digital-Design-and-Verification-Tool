from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage_history
from zddv.config import ProjectConfig
from zddv.coverage import (
    merge_coverage,
    merge_xcelium_coverage,
    parse_xcelium_imc_summary,
)
from zddv.storage import list_coverage_score_snapshots


IMC_SUMMARY = """IMC(64): test build
Starting batch mode
Legend: Metric* means cumulative
name Overall* Average Overall* Covered Code* Average Code* Covered Fsm* Average Fsm* Covered Functional* Average Functional* Covered
--------------------------------------------------------------------------------------------------------------------------------
tb_top 86.25% 82.50% (33/40) 80.00% 75.00% (18/24) n/a n/a 92.50% 90.00% (9/10)
"""


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="xcelium",
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
        waveform=False,
        coverage=True,
    )


def _coverage_run(project: ProjectConfig, name: str) -> Path:
    path = project.root / project.run_dir / name / "cov_work" / "scope" / "zddv"
    path.mkdir(parents=True)
    (path / f"icc_{name}.ucm").write_text("model", encoding="utf-8")
    (path / f"icc_{name}.ucd").write_text("data", encoding="utf-8")
    return path.resolve()


def test_parse_xcelium_imc_summary_normalizes_explicit_overall_scores():
    metrics = parse_xcelium_imc_summary(IMC_SUMMARY)

    assert metrics["scope"] == "tb_top"
    assert metrics["tool_total_coverage"] == pytest.approx(82.50)
    assert metrics["by_metric"]["overall_average"] == pytest.approx(86.25)
    assert metrics["by_metric"]["overall_covered"] == pytest.approx(82.50)
    assert metrics["metric_semantics"] == "imc-summary-overall"


def test_parse_xcelium_imc_summary_rejects_unknown_layout():
    with pytest.raises(ValueError, match="Overall Average/Covered"):
        parse_xcelium_imc_summary("name Some Other Columns\ntb 90.0%\n")


def test_merge_xcelium_coverage_uses_imc_and_persists_score_history(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    inputs = [
        _coverage_run(project, "run-a"),
        _coverage_run(project, "run-b"),
    ]
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    commands: list[list[str]] = []

    def fake_run(command, cwd):
        commands.append(list(command))
        cwd = Path(cwd)
        if "-load" not in command:
            assert command[:2] == ["/opt/cadence/bin/imc", "-execcmd"]
            assert "merge -overwrite -runfile" in command[2]
            assert "-out merged -metrics all" in command[2]
            merged = cwd / "cov_work" / "scope" / "merged"
            merged.mkdir(parents=True)
            (merged / "icc_merged.ucm").write_text("model", encoding="utf-8")
            (merged / "icc_merged.ucd").write_text("data", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge complete\n")

        assert command[command.index("-load") + 1].endswith(
            "cov_work/scope/merged"
        )
        assert command[command.index("-execcmd") + 1] == (
            "report -summary -cumulative on -inst -local off; exit"
        )
        return SimpleNamespace(returncode=0, stdout=IMC_SUMMARY)

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_xcelium_coverage(project)

    assert result["inputs"] == [str(path) for path in inputs]
    assert Path(result["merged"]).is_dir()
    assert Path(result["summary"]).read_text(encoding="utf-8") == IMC_SUMMARY
    assert result["metrics_status"] == "normalized"
    assert result["metrics"]["tool_total_coverage"] == pytest.approx(82.50)
    assert result["snapshot_id"] is not None
    assert len(commands) == 2

    runfile = Path(result["runfile"]).read_text(encoding="utf-8").splitlines()
    assert len(runfile) == 2
    assert all(line.endswith(".ucd") for line in runfile)

    snapshots = list_coverage_score_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["score"] == pytest.approx(82.50)
    assert snapshots[0]["by_metric"]["overall_average"] == pytest.approx(86.25)

    manifest = json.loads(
        Path(result["metrics_path"]).read_text(encoding="utf-8")
    )
    assert manifest["simulator"] == "xcelium"
    assert manifest["metrics_status"] == "normalized"
    assert manifest["input_count"] == 2
    assert manifest["metrics"]["scope"] == "tb_top"


def test_merge_coverage_dispatches_xcelium(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    expected = {"inputs": [], "merged": "merged"}

    monkeypatch.setattr(
        "zddv.coverage.merge_xcelium_coverage",
        lambda loaded: expected if loaded is project else None,
    )

    assert merge_coverage(project) is expected


def test_xcelium_coverage_history_uses_score_snapshots(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")

    from zddv.storage import record_coverage_score_snapshot

    record_coverage_score_snapshot(
        project,
        {
            "snapshot_id": "cov-score-xcelium-test",
            "created_at": "2026-09-22T10:00:00+00:00",
            "project": project.name,
            "simulator": project.simulator,
            "input_count": 1,
            "score": 82.5,
            "by_metric": {
                "overall_average": 86.25,
                "overall_covered": 82.5,
            },
            "by_metric_counts": {},
            "merged": "/tmp/merged",
            "summary": "/tmp/summary.txt",
            "metrics_path": "/tmp/metrics.json",
        },
    )
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)

    rc = cmd_coverage_history(
        SimpleNamespace(project=str(project.root), limit=5)
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "82.50%" in output
    assert "cov-score-xcelium-test" in output
    assert "overall_average=86.25%" in output

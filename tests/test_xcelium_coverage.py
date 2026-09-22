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


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="xcelium",
        coverage=True,
        waveform=False,
    )


def _coverage_run(project: ProjectConfig, run_id: str) -> Path:
    path = (
        project.root
        / project.run_dir
        / run_id
        / "coverage"
        / run_id
    ).resolve()
    path.mkdir(parents=True)
    (path / f"{run_id}.ucd").write_text("fixture\n", encoding="utf-8")
    return path


def test_merge_xcelium_coverage_uses_imc_and_retains_evidence(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    first = _coverage_run(project, "run-a")
    second = _coverage_run(project, "run-b")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    captured: dict[str, object] = {}

    def fake_run(command, cwd):
        captured["command"] = list(command)
        captured["cwd"] = Path(cwd)
        script_path = Path(command[command.index("-exec") + 1])
        captured["script"] = script_path.read_text(encoding="utf-8")

        out_dir = project.root / ".zddv" / "coverage"
        merged = out_dir / "xcelium-imc-merged"
        merged.mkdir(parents=True)
        (merged / "merged.ucd").write_text("merged\n", encoding="utf-8")
        report_dir = out_dir / "xcelium-imc-report"
        (report_dir / "summary.txt").write_text(
            """IMC(64): test build
Starting batch mode
Legend: Metric* means cumulative
name Overall* Average Overall* Covered Code* Average Code* Covered Fsm* Average Fsm* Covered Functional* Average Functional* Covered
--------------------------------------------------------------------------------------------------------------------------------
tb_top 86.25% 82.50% (33/40) 80.00% 75.00% (18/24) n/a n/a 92.50% 90.00% (9/10)
""",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="IMC merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_coverage(project)

    assert result["inputs"] == [str(first), str(second)]
    assert result["metrics_status"] == "normalized"
    assert result["metrics"]["tool_total_coverage"] == pytest.approx(82.50)
    assert result["metrics"]["by_metric"]["overall_average"] == pytest.approx(86.25)
    assert result["metrics"]["by_metric"]["overall_covered"] == pytest.approx(82.50)
    assert result["metrics"]["by_metric"]["code_average"] == pytest.approx(80.0)
    assert result["metrics"]["by_metric"]["code_covered"] == pytest.approx(75.0)
    assert "fsm_average" not in result["metrics"]["by_metric"]
    assert result["metrics"]["by_metric"]["functional_covered"] == pytest.approx(90.0)
    assert result["metrics"]["by_metric_counts"]["overall_covered"] == {
        "covered": 33,
        "total": 40,
        "hit_rate": pytest.approx(82.5),
    }
    assert result["metrics"]["by_metric_counts"]["code_covered"]["covered"] == 18
    assert result["metrics"]["by_metric_counts"]["functional_covered"]["total"] == 10
    assert Path(result["merged"]).name == "xcelium-imc-merged"
    assert Path(result["summary"]).name == "summary.txt"

    command = captured["command"]
    assert command[:3] == ["/opt/cadence/bin/imc", "-batch", "-exec"]
    script = str(captured["script"])
    assert "merge -out" in script
    assert "-overwrite" in script
    assert first.as_posix() in script
    assert second.as_posix() in script
    assert "load -run" in script
    assert 'report -summary -inst "*..."' in script
    assert "-metrics all" in script
    assert "-cumulative on" in script
    assert "-showempty on" in script
    assert "-local off" in script
    assert script.rstrip().endswith("exit")

    manifest = json.loads(
        Path(result["metrics_path"]).read_text(encoding="utf-8")
    )
    assert manifest["status"] == "merged-report-captured"
    assert manifest["metrics_status"] == "normalized"
    assert manifest["input_count"] == 2
    assert len(manifest["merged_ucd_files"]) == 1
    assert manifest["snapshot_id"] == result["snapshot_id"]
    assert manifest["metrics"]["scope"] == "tb_top"

    snapshots = list_coverage_score_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["score"] == pytest.approx(82.50)
    assert snapshots[0]["by_metric"]["overall_average"] == pytest.approx(86.25)
    assert snapshots[0]["by_metric"]["code_covered"] == pytest.approx(75.0)
    assert snapshots[0]["by_metric_counts"]["overall_covered"]["covered"] == 33
    assert snapshots[0]["by_metric_counts"]["functional_covered"]["total"] == 10


def test_merge_xcelium_coverage_requires_native_run_database(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    with pytest.raises(RuntimeError, match=r"No Xcelium \.ucd run databases"):
        merge_xcelium_coverage(project)


def test_merge_xcelium_coverage_rejects_missing_merged_ucd(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )
    monkeypatch.setattr(
        "zddv.coverage._run",
        lambda command, cwd: SimpleNamespace(
            returncode=0,
            stdout="IMC returned success without a merged database\n",
        ),
    )

    with pytest.raises(RuntimeError, match="IMC coverage merge/report failed"):
        merge_xcelium_coverage(project)


def test_merge_xcelium_coverage_requires_imc(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr("zddv.coverage.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="Cadence IMC was not found"):
        merge_xcelium_coverage(project)



def test_parse_xcelium_imc_summary_preserves_reported_counts():
    report = """Legend: Metric* means cumulative
name Overall* Average Overall* Covered Code* Average Code* Covered Fsm* Average Fsm* Covered Functional* Average Functional* Covered
-------------------------------------------------------------------------------------------------------------------------------
dut 91.25% 89.00% (89/100) 80.00% 75.00% (75/100) n/a n/a 100.00% 100.00% (8/8)
"""

    metrics = parse_xcelium_imc_summary(report)

    assert metrics["tool_total_coverage"] == pytest.approx(89.0)
    assert metrics["by_metric"]["overall_average"] == pytest.approx(91.25)
    assert metrics["by_metric"]["overall_covered"] == pytest.approx(89.0)
    assert metrics["by_metric"]["code_average"] == pytest.approx(80.0)
    assert metrics["by_metric"]["functional_covered"] == pytest.approx(100.0)
    assert "fsm_covered" not in metrics["by_metric"]
    assert metrics["by_metric_counts"]["overall_covered"] == {
        "covered": 89,
        "total": 100,
        "hit_rate": pytest.approx(89.0),
    }
    assert metrics["by_metric_counts"]["functional_covered"]["total"] == 8


def test_parse_xcelium_imc_summary_requires_documented_header():
    with pytest.raises(ValueError, match="Overall Average/Covered"):
        parse_xcelium_imc_summary("name Other Columns\ntb_top 90.0%\n")


def test_xcelium_coverage_history_uses_score_snapshots(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
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

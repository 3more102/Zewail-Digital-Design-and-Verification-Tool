from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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
            """IMC(64): 24.03 test fixture
Legend: Metric* means cumulative
name Overall* Average Overall* Covered Code* Average Code* Covered Fsm* Average Fsm* Covered Functional* Average Functional* Covered
--------------------------------------------------------------------------------------------------------------------------------
tb 100.00% 97.50% (195/200) 98.00% 96.00% (96/100) n/a n/a 91.00% 90.00% (90/100)
""",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="IMC merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_coverage(project)

    assert result["inputs"] == [str(first), str(second)]
    assert result["metrics_status"] == "normalized"
    assert result["metrics"]["tool_total_coverage"] == pytest.approx(97.50)
    assert result["metrics"]["by_metric"]["code"] == pytest.approx(96.00)
    assert result["metrics"]["by_metric"]["functional"] == pytest.approx(90.00)
    assert "fsm" not in result["metrics"]["by_metric"]
    assert result["metrics"]["by_metric_counts"]["overall"] == {
        "covered": 195,
        "total": 200,
        "hit_rate": pytest.approx(97.5),
    }
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
    assert "report -summary -cumulative on -inst -local off" in script
    assert script.rstrip().endswith("exit")

    manifest = json.loads(
        Path(result["metrics_path"]).read_text(encoding="utf-8")
    )
    assert manifest["status"] == "merged-report-captured"
    assert manifest["metrics_status"] == "normalized"
    assert manifest["input_count"] == 2
    assert len(manifest["merged_ucd_files"]) == 1
    assert manifest["snapshot_id"] == result["snapshot_id"]
    assert manifest["metrics"]["tool_total_coverage"] == pytest.approx(97.50)

    snapshots = list_coverage_score_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["score"] == pytest.approx(97.50)
    assert snapshots[0]["by_metric"]["code"] == pytest.approx(96.00)


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

def test_parse_xcelium_imc_summary_matches_documented_ascii_shape():
    text = """IMC(64): 14.21-s070
*I,RUNLD: Successfully loaded run cov_work/scope/test.
Starting batch mode
Legend: Metric* means cumulative e.g. Block* means Cumulative Block Coverage
name Overall* Average Overall* Covered Code* Average Code* Covered Fsm* Average Fsm* Covered Functional* Average Functional* Covered
--------------------------------------------------------------------------------------------------------------------------------
tb 100.00% 100.00% (2/2) n/a n/a n/a n/a 100.00% 100.00% (2/2)
"""

    metrics = parse_xcelium_imc_summary(text)

    assert metrics["scope"] == "tb"
    assert metrics["tool_total_coverage"] == pytest.approx(100.0)
    assert metrics["by_metric"] == {"functional": pytest.approx(100.0)}
    assert metrics["by_metric_counts"]["overall"] == {
        "covered": 2,
        "total": 2,
        "hit_rate": pytest.approx(100.0),
    }
    assert metrics["by_metric_counts"]["functional"]["covered"] == 2
    assert metrics["grades"]["code"] == {"average": None, "covered": None}
    assert metrics["grades"]["fsm"] == {"average": None, "covered": None}


def test_parse_xcelium_imc_summary_preserves_three_part_count_without_guessing():
    text = """name Overall Average Overall Covered Code Average Code Covered Fsm Average Fsm Covered Functional Average Functional Covered
tb 96.54% 96.08% (58216/60590/5041) 95.00% 94.00% n/a n/a 90.00% 89.00%
"""

    metrics = parse_xcelium_imc_summary(text)

    assert "overall" not in metrics["by_metric_counts"]
    assert metrics["count_evidence"]["overall"] == {
        "raw": "(58216/60590/5041)",
        "parts": [58216, 60590, 5041],
    }
    assert metrics["tool_total_coverage"] == pytest.approx(96.08)


def test_parse_xcelium_imc_summary_rejects_unknown_shape():
    with pytest.raises(ValueError, match="header"):
        parse_xcelium_imc_summary("IMC summary fixture without documented columns\n")


def test_parse_xcelium_imc_summary_rejects_out_of_range_grade():
    text = """name Overall Average Overall Covered Code Average Code Covered Fsm Average Fsm Covered Functional Average Functional Covered
tb 100.00% 101.00% (101/101) n/a n/a n/a n/a n/a n/a
"""

    with pytest.raises(ValueError, match="outside 0..100"):
        parse_xcelium_imc_summary(text)


def test_parse_xcelium_imc_summary_rejects_impossible_two_part_count():
    text = """name Overall Average Overall Covered Code Average Code Covered Fsm Average Fsm Covered Functional Average Functional Covered
tb 100.00% 100.00% (3/2) n/a n/a n/a n/a n/a n/a
"""

    with pytest.raises(ValueError, match="exceeds total"):
        parse_xcelium_imc_summary(text)


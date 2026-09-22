from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.coverage import (
    merge_questa_coverage,
    parse_questa_coverage_summary,
    parse_questa_functional_coverage,
)
from zddv.storage import (
    list_coverage_snapshots,
    list_functional_coverage_bins,
    list_functional_coverage_snapshots,
)


QUESTA_SUMMARY = """QuestaSim-64 vcover 2024.2 Coverage Utility 2024.05 May 20 2024
Coverage Report Totals BY INSTANCES: Number of Instances 23

    Enabled Coverage              Bins      Hits    Misses    Weight  Coverage
    ----------------              ----      ----    ------    ------  --------
    Branches                      3044      2982        62         1    97.96%
    Expressions                   1665      1143       522         1    68.64%
    Statements                    4920      4920         0         1   100.00%
    Toggles                      72906     37574     35332         1    51.53%
Total coverage (filtered view): 79.53%
"""

QUESTA_CVG_DETAILS = r"""COVERGROUP COVERAGE:
Covergroup instance \top/dut/fifo/ram_cvg1 50.00% 100 - Uncovered
    covered/total bins: 2 4
    missing/total bins: 2 4
    % Hit: 50.00% 100
    Coverpoint we_cp 50.00% 10 - Uncovered
        covered/total bins: 1 2
        missing/total bins: 1 2
        bin invalid 0 1 - ZERO
        bin valid 4 1 - Covered
        ignore_bin inval 0 - ZERO
    Cross waddrXpush 0.00% 100 - ZERO
        bin cross_zero 0 1 - ZERO
"""


def test_parse_questa_functional_coverage_bins():
    payload = parse_questa_functional_coverage(QUESTA_CVG_DETAILS)

    assert payload["source"] == "questa-vcover"
    assert len(payload["bins"]) == 3
    assert payload["bins"][0] == {
        "scope": r"\top/dut/fifo/ram_cvg1",
        "coverpoint": "we_cp",
        "bin": "invalid",
        "hits": 0,
        "goal": 1,
        "metadata": {
            "simulator": "questa",
            "point_kind": "coverpoint",
            "reported_status": "ZERO",
        },
    }
    assert payload["bins"][1]["bin"] == "valid"
    assert payload["bins"][1]["hits"] == 4
    assert payload["bins"][2]["coverpoint"] == "waddrXpush"
    assert payload["bins"][2]["metadata"]["point_kind"] == "cross"


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="questa",
        rtl=[],
        tb=[],
        waveform=False,
        coverage=True,
    )


def test_parse_questa_summary_preserves_tool_score_separately():
    metrics = parse_questa_coverage_summary(QUESTA_SUMMARY)

    assert metrics["total_points"] == 82535
    assert metrics["hit_points"] == 46619
    assert metrics["unhit_points"] == 35916
    assert metrics["hit_rate"] == pytest.approx(100.0 * 46619 / 82535)
    assert metrics["tool_total_coverage"] == 79.53
    assert metrics["by_type"]["branch"] == {
        "total": 3044,
        "hit": 2982,
        "hit_rate": 97.96,
    }
    assert metrics["by_type"]["statement"]["hit_rate"] == 100.0
    assert metrics["by_type"]["toggle"]["hit"] == 37574


def test_merge_questa_coverage_merges_reports_and_persists_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_root = (project.root / project.run_dir).resolve()
    for name in ("run-a", "run-b"):
        run_dir = run_root / name
        run_dir.mkdir(parents=True)
        (run_dir / "coverage.ucdb").write_text(
            f"{name} fixture\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/questa/bin/vcover" if name == "vcover" else None,
    )

    commands: list[list[str]] = []

    def fake_run(command, cwd):
        commands.append(list(command))
        if command[1] == "merge":
            out_path = Path(command[command.index("-out") + 1])
            out_path.write_text("merged fixture\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge complete\n")
        if command[1:3] == ["report", "-summary"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_SUMMARY)
        if command[1] == "report" and "-cvg" in command:
            return SimpleNamespace(returncode=0, stdout=QUESTA_CVG_DETAILS)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_questa_coverage(project)

    assert commands[0][:4] == [
        "/opt/questa/bin/vcover",
        "merge",
        "-out",
        result["merged"],
    ]
    assert commands[1] == [
        "/opt/questa/bin/vcover",
        "report",
        "-summary",
        result["merged"],
    ]
    assert commands[2] == [
        "/opt/questa/bin/vcover",
        "report",
        "-cvg",
        "-details",
        "-noignorebin",
        "-nozeroweights",
        result["merged"],
    ]
    assert len(result["inputs"]) == 2
    assert Path(result["merged"]).exists()
    assert Path(result["summary"]).read_text(encoding="utf-8") == QUESTA_SUMMARY

    payload = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))
    assert payload["simulator"] == "questa"
    assert payload["input_count"] == 2
    assert payload["tool_total_coverage"] == 79.53
    assert payload["by_type"]["expression"]["hit"] == 1143

    snapshots = list_coverage_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["simulator"] == "questa"
    assert snapshots[0]["total_points"] == 82535
    assert snapshots[0]["hit_points"] == 46619

    assert result["functional_bin_count"] == 3
    assert result["functional_snapshot_id"] is not None
    assert Path(result["functional_report"]).read_text(
        encoding="utf-8"
    ) == QUESTA_CVG_DETAILS

    fcov_snapshots = list_functional_coverage_snapshots(project, limit=5)
    assert len(fcov_snapshots) == 1
    assert fcov_snapshots[0]["source"] == "questa-vcover"
    assert fcov_snapshots[0]["total_bins"] == 3
    assert fcov_snapshots[0]["covered_bins"] == 1

    holes = list_functional_coverage_bins(
        project,
        fcov_snapshots[0]["snapshot_id"],
        status="UNCOVERED",
    )
    assert {item["bin_name"] for item in holes} == {"invalid", "cross_zero"}

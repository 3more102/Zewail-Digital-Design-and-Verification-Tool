from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import ProjectConfig, save_project
from zddv.formal.coverage import aggregate_formal_cover_coverage
from zddv.formal.results import analyze_formal_result_file


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "dut.sv").write_text(
        "module dut(input logic clk); endmodule\n",
        encoding="utf-8",
    )
    project = ProjectConfig(
        root=root,
        name="demo",
        top="dut",
        simulator="verilator",
        rtl=["rtl/*.sv"],
        tb=[],
        waveform=False,
        coverage=False,
    )
    save_project(project)
    return project


def _payload(
    *,
    depth: int = 20,
    seen: str = "COVERED",
    missing: str = "UNCOVERED",
    complete: bool = True,
) -> dict:
    return {
        "backend": "sby",
        "engine": "smtbmc",
        "request": {
            "mode": "cover",
            "depth": depth,
            "properties": [],
            "timeout_s": 30.0,
        },
        "command": ["sby", "job.sby"],
        "returncode": 0,
        "status": "PASS" if missing == "COVERED" else "FAIL",
        "run_dir": ".zddv/formal/runs/run",
        "log_path": ".zddv/formal/runs/run/formal.log",
        "properties": [
            {
                "name": "dut.c_seen",
                "kind": "cover",
                "status": seen,
                "depth": 5 if seen == "COVERED" else depth,
            },
            {
                "name": "dut.c_missing",
                "kind": "cover",
                "status": missing,
                "depth": depth,
            },
        ],
        "artifacts": ["property-status.jsonl"],
        "property_set_complete": complete,
    }


def _persist(project: ProjectConfig, name: str, payload: dict) -> dict:
    source = project.root / f"{name}.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    return analyze_formal_result_file(
        project,
        source,
        output=project.root / ".zddv" / "formal" / f"{name}-report.json",
    )


def test_formal_coverage_aggregates_only_exactly_compatible_snapshots(
    tmp_path: Path,
):
    project = _project(tmp_path)
    first = _persist(project, "run1", _payload())
    second = _persist(
        project,
        "run2",
        _payload(missing="COVERED"),
    )

    result = aggregate_formal_cover_coverage(project)

    assert result["eligible_snapshots"] == 2
    assert result["excluded_snapshots"] == {}
    assert len(result["groups"]) == 1

    group = result["groups"][0]
    assert group["depth"] == 20
    assert group["snapshot_count"] == 2
    assert group["property_count"] == 2
    assert group["covered_goals"] == 2
    assert group["unreached_goals"] == 0
    assert group["coverage_rate"] == pytest.approx(100.0)
    assert set(group["snapshots"]) == {
        first["snapshot_id"],
        second["snapshot_id"],
    }

    by_name = {item["name"]: item for item in group["properties"]}
    assert by_name["dut.c_missing"]["status"] == "COVERED"
    assert by_name["dut.c_missing"]["covered_runs"] == 1
    assert by_name["dut.c_missing"]["uncovered_runs"] == 1


def test_formal_coverage_separates_depths_and_design_revisions(tmp_path: Path):
    project = _project(tmp_path)
    first = _persist(project, "depth20-a", _payload(depth=20))
    _persist(project, "depth40", _payload(depth=40))

    (project.root / "rtl" / "dut.sv").write_text(
        "module dut(input logic clk, input logic rst_n); endmodule\n",
        encoding="utf-8",
    )
    revised = _persist(project, "depth20-b", _payload(depth=20))

    result = aggregate_formal_cover_coverage(project)

    assert result["eligible_snapshots"] == 3
    assert len(result["groups"]) == 3
    depth20 = [group for group in result["groups"] if group["depth"] == 20]
    assert len(depth20) == 2
    assert first["design_fingerprint"] != revised["design_fingerprint"]
    assert {group["design_fingerprint"] for group in depth20} == {
        first["design_fingerprint"],
        revised["design_fingerprint"],
    }


def test_formal_coverage_excludes_incomplete_property_universes(tmp_path: Path):
    project = _project(tmp_path)
    _persist(project, "incomplete", _payload(complete=False))

    result = aggregate_formal_cover_coverage(project)

    assert result["eligible_snapshots"] == 0
    assert result["groups"] == []
    assert result["excluded_snapshots"] == {"incomplete_property_set": 1}


def test_formal_coverage_cli_writes_reviewable_report(
    tmp_path: Path,
    capsys,
):
    project = _project(tmp_path)
    _persist(project, "run1", _payload())

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-coverage",
            "--depth",
            "20",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert "FORMAL COVERAGE: groups=1 eligible=1 considered=1" in output
    assert "covered=1/2 (50.00%)" in output
    assert "unreached=1 unresolved=0" in output

    report = project.root / ".zddv" / "formal" / "coverage.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["scope"] == "FINITE_DEPTH_COVER_REACHABILITY"
    assert payload["groups"][0]["coverage_rate"] == pytest.approx(50.0)

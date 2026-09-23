from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.desktop import build_desktop_snapshot
from zddv.storage import (
    record_coverage_score_snapshot,
    record_coverage_snapshot,
    record_run,
)


def _run_record(run_id: str, status: str, *, seed: int) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-23T06:{seed:02d}:00+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator 5.x",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 12.5,
        "run_dir": f".zddv/runs/{run_id}",
        "log": f".zddv/runs/{run_id}/simulation.log",
        "waveform": None,
        "coverage": None,
        "timeout_s": 30.0,
        "command": ["sim"],
        "plusargs": [],
    }


def test_desktop_snapshot_summarizes_persisted_evidence(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass", "PASS", seed=1))
    record_run(project, _run_record("run-fail", "FAIL", seed=2))
    record_coverage_snapshot(
        project,
        {
            "snapshot_id": "cov-points",
            "created_at": "2026-09-23T06:10:00+00:00",
            "project": "demo",
            "simulator": "verilator",
            "input_count": 2,
            "total_points": 100,
            "hit_points": 80,
            "hit_rate": 80.0,
            "by_type": {"line": {"total": 100, "hit": 80, "hit_rate": 80.0}},
            "merged": ".zddv/coverage/merged.dat",
            "summary": ".zddv/coverage/summary.txt",
            "metrics_path": ".zddv/coverage/metrics.json",
        },
    )
    record_coverage_score_snapshot(
        project,
        {
            "snapshot_id": "cov-score",
            "created_at": "2026-09-23T06:20:00+00:00",
            "project": "demo",
            "simulator": "questa",
            "input_count": 2,
            "score": 93.5,
            "by_metric": {"total": 93.5},
            "by_metric_counts": {},
            "merged": ".zddv/coverage/coverage.ucdb",
            "summary": ".zddv/coverage/summary.txt",
            "metrics_path": ".zddv/coverage/metrics.json",
        },
    )

    snapshot = build_desktop_snapshot(project, limit=10)

    assert snapshot["stats"]["total"] == 2
    assert snapshot["stats"]["passed"] == 1
    assert snapshot["stats"]["failed"] == 1
    assert [row["run_id"] for row in snapshot["recent_runs"]] == [
        "run-fail",
        "run-pass",
    ]
    assert snapshot["failure_groups"][0]["count"] == 1
    assert snapshot["latest_coverage"]["snapshot_id"] == "cov-score"
    assert snapshot["latest_coverage"]["percent"] == 93.5
    assert snapshot["latest_formal"] is None
    assert snapshot["latest_uvm"] is None
    assert snapshot["policy"] == {
        "display_only": True,
        "executes_verification": False,
        "invokes_ai": False,
        "applies_generated_artifacts": False,
        "executes_elaboration": False,
        "edits_sources": False,
    }


def test_desktop_snapshot_rejects_nonpositive_limit(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    with pytest.raises(ValueError, match="limit must be >= 1"):
        build_desktop_snapshot(project, limit=0)


def test_desktop_cli_launches_viewer_with_requested_limit(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = initialize_project(tmp_path / "demo")
    captured = {}

    def fake_launch(project_arg, *, limit):
        captured["root"] = project_arg.root
        captured["limit"] = limit
        return {
            "stats": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "timed_out": 0,
                "pass_rate": 0.0,
            }
        }

    monkeypatch.setattr("zddv.cli.launch_desktop_gui", fake_launch)

    rc = main(["--project", str(project.root), "gui", "--limit", "7"])

    assert rc == 0
    assert captured == {"root": project.root, "limit": 7}
    assert "GUI CLOSED" in capsys.readouterr().out



def test_desktop_snapshot_indexes_sources_without_writing_design_artifact(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "rtl" / "top.sv"
    source.write_text(
        """
module leaf;
endmodule

module top;
    leaf u_leaf();
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)

    snapshot = build_desktop_snapshot(project)
    source_index = snapshot["design"]["source_index"]

    assert source_index["summary"] == {
        "files": 1,
        "units": 2,
        "instances": 1,
        "duplicate_unit_names": 0,
    }
    assert source_index["hierarchy"]["path"] == "top"
    assert source_index["hierarchy"]["children"][0]["path"] == "top.u_leaf"
    assert snapshot["design"]["elaborated"] is None
    assert not (project.root / ".zddv" / "design" / "index.json").exists()


def test_desktop_snapshot_reads_existing_elaborated_hierarchy_without_execution(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "rtl" / "top.sv"
    source.write_text("module top; endmodule\n", encoding="utf-8")
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)

    design_dir = project.root / ".zddv" / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    artifact = design_dir / "elaborated.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": project.name,
                "top": project.top,
                "simulator": project.simulator,
                "instances": [
                    {
                        "path": "top",
                        "name": "top",
                        "module": "top",
                        "top": True,
                        "location": {"path": "rtl/top.sv", "line": 1},
                    }
                ],
                "modules": [],
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_desktop_snapshot(project)
    elaborated = snapshot["design"]["elaborated"]

    assert elaborated["status"] == "AVAILABLE"
    assert elaborated["path"] == str(artifact.resolve())
    assert elaborated["index"]["instances"][0]["path"] == "top"

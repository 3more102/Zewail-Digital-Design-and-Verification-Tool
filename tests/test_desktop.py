from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.desktop import build_desktop_snapshot, read_source_text
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


def _project_with_hierarchy(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "rtl" / "design.sv"
    source.write_text(
        """module leaf;
endmodule

module child;
    leaf u_leaf();
endmodule

module tb_top;
    child u_child();
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "tb_top"
    save_project(project)
    return project


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
        "writes_project_artifacts": False,
    }


def test_desktop_snapshot_adds_source_and_source_hierarchy(tmp_path: Path):
    project = _project_with_hierarchy(tmp_path)

    snapshot = build_desktop_snapshot(project, limit=10)
    design = snapshot["design"]

    assert design["summary"] == {
        "files": 1,
        "units": 3,
        "instances": 2,
        "duplicate_unit_names": 0,
    }
    assert design["files"][0]["path"] == "rtl/design.sv"
    assert [row["path"] for row in design["source_hierarchy_rows"]] == [
        "tb_top",
        "tb_top.u_child",
        "tb_top.u_child.u_leaf",
    ]
    assert design["source_hierarchy_rows"][1]["file"] == "rtl/design.sv"
    assert design["elaborated"]["status"] == "NOT_PRESENT"


def test_desktop_uses_matching_persisted_elaborated_evidence(tmp_path: Path):
    project = _project_with_hierarchy(tmp_path)
    out_dir = project.root / ".zddv" / "design"
    out_dir.mkdir(parents=True)
    (out_dir / "elaborated.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": project.name,
                "top": project.top,
                "simulator": project.simulator,
                "simulator_version": "Verilator test",
                "source_format": "json",
                "modules": [
                    {"name": "tb_top", "top": True},
                    {"name": "child", "top": False},
                ],
                "instances": [
                    {
                        "path": "tb_top",
                        "name": "tb_top",
                        "module": "tb_top",
                        "top": True,
                        "location": {"path": "rtl/design.sv", "line": 8},
                    },
                    {
                        "path": "tb_top.u_child",
                        "name": "u_child",
                        "module": "child",
                        "top": False,
                        "location": {"path": "rtl/design.sv", "line": 9},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    elaborated = build_desktop_snapshot(project)["design"]["elaborated"]

    assert elaborated["status"] == "PRESENT"
    assert elaborated["source_format"] == "json"
    assert elaborated["summary"] == {"modules": 2, "instances": 2}
    assert elaborated["instances"][1]["path"] == "tb_top.u_child"


def test_desktop_rejects_stale_elaborated_evidence(tmp_path: Path):
    project = _project_with_hierarchy(tmp_path)
    out_dir = project.root / ".zddv" / "design"
    out_dir.mkdir(parents=True)
    (out_dir / "elaborated.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": project.name,
                "top": "old_top",
                "simulator": project.simulator,
                "modules": [],
                "instances": [],
            }
        ),
        encoding="utf-8",
    )

    elaborated = build_desktop_snapshot(project)["design"]["elaborated"]

    assert elaborated["status"] == "STALE"
    assert "top='old_top'" in elaborated["reason"]
    assert elaborated["instances"] == []


def test_source_reader_is_limited_to_configured_sources(tmp_path: Path):
    project = _project_with_hierarchy(tmp_path)

    payload = read_source_text(project, "rtl/design.sv")
    assert payload["path"] == "rtl/design.sv"
    assert "module tb_top;" in payload["text"]

    secret = project.root / "secret.txt"
    secret.write_text("not a configured source", encoding="utf-8")
    with pytest.raises(ValueError, match="not part of the configured ZDDV project"):
        read_source_text(project, "secret.txt")


def test_desktop_snapshot_rejects_nonpositive_limit(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    with pytest.raises(ValueError, match="limit must be >= 1"):
        build_desktop_snapshot(project, limit=0)


def test_gui_cli_launches_viewer_with_requested_limit(
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

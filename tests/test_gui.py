from pathlib import Path
import json

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.gui import (
    build_debug_gui_snapshot,
    hierarchy_rows,
    source_excerpt,
)
from zddv.storage import record_coverage_snapshot, record_run


def _project_with_sources(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    rtl = project.root / "rtl"
    rtl.mkdir()
    (rtl / "top.sv").write_text(
        """module child(input logic a, output logic y);
  assign y = a;
endmodule

module tb_top;
  logic a;
  logic y;
  child u_child(.a(a), .y(y));
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "tb_top"
    save_project(project)
    return project


def _run(run_id: str, status: str, seed: int, log: Path) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-23T06:30:0{seed}+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 12.0 + seed,
        "run_dir": str(log.parent),
        "log": str(log),
        "waveform": None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def test_debug_gui_snapshot_is_read_only_backend_view(tmp_path: Path):
    project = _project_with_sources(tmp_path)
    pass_log = tmp_path / "pass.log"
    fail_log = tmp_path / "fail.log"
    pass_log.write_text("PASS\n", encoding="utf-8")
    fail_log.write_text(
        "ASSERT mismatch packet=41 expected=42 actual=43\n",
        encoding="utf-8",
    )

    record_run(project, _run("run-1", "PASS", 1, pass_log))
    record_run(project, _run("run-2", "FAIL", 2, fail_log))
    record_coverage_snapshot(
        project,
        {
            "snapshot_id": "cov-gui",
            "created_at": "2026-09-23T06:31:00+00:00",
            "project": "demo",
            "simulator": "verilator",
            "input_count": 2,
            "total_points": 20,
            "hit_points": 18,
            "hit_rate": 90.0,
            "by_type": {
                "line": {"total": 20, "hit": 18, "hit_rate": 90.0},
            },
            "merged": "/tmp/coverage.dat",
            "summary": "/tmp/summary.txt",
            "metrics_path": "/tmp/metrics.json",
        },
    )

    snapshot = build_debug_gui_snapshot(project, limit=10)

    assert snapshot["project"] == "demo"
    assert snapshot["stats"]["total"] == 2
    assert snapshot["stats"]["passed"] == 1
    assert snapshot["stats"]["failed"] == 1
    assert snapshot["stats"]["pass_rate"] == 50.0
    assert [row["run_id"] for row in snapshot["runs"]] == ["run-2", "run-1"]
    assert snapshot["failure_groups"][0]["signature"] == (
        "ASSERT mismatch packet=# expected=# actual=#"
    )
    assert snapshot["latest_coverage"]["snapshot_id"] == "cov-gui"
    assert snapshot["latest_coverage"]["percent"] == 90.0
    assert snapshot["policy"]["writes_project_files"] is False
    assert snapshot["design"]["source_index"]["summary"]["units"] == 2
    assert snapshot["design"]["elaborated"]["status"] == "NOT_PRESENT"

    rows = hierarchy_rows(snapshot)
    assert [row["path"] for row in rows] == ["tb_top", "tb_top.u_child"]
    assert all(row["source"] == "source" for row in rows)


def test_persisted_elaborated_hierarchy_is_preferred(tmp_path: Path):
    project = _project_with_sources(tmp_path)
    out = project.root / ".zddv" / "design"
    out.mkdir(parents=True)
    (out / "elaborated.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": "2026-09-23T06:40:00+00:00",
                "simulator": "verilator",
                "simulator_version": "Verilator 5.x",
                "source_format": "json",
                "modules": [{"name": "tb_top"}],
                "instances": [
                    {
                        "path": "tb_top",
                        "name": "tb_top",
                        "module": "tb_top",
                        "top": True,
                        "location": {"file": "rtl/top.sv", "line": 5},
                    },
                    {
                        "path": "tb_top.u_child",
                        "name": "u_child",
                        "module": "child",
                        "top": False,
                        "location": {"file": "rtl/top.sv", "line": 8},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_debug_gui_snapshot(project)
    rows = hierarchy_rows(snapshot)

    assert snapshot["design"]["elaborated"]["status"] == "PRESENT"
    assert [row["path"] for row in rows] == ["tb_top", "tb_top.u_child"]
    assert all(row["source"] == "elaborated" for row in rows)


def test_source_excerpt_is_bounded_to_configured_sources(tmp_path: Path):
    project = _project_with_sources(tmp_path)

    excerpt = source_excerpt(project, "rtl/top.sv", 6, radius=1)
    assert excerpt["start_line"] == 5
    assert excerpt["end_line"] == 7
    assert "module tb_top;" in excerpt["text"]

    outside = tmp_path / "outside.sv"
    outside.write_text("module outside; endmodule\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not part of the configured project"):
        source_excerpt(project, str(outside), 1)


def test_debug_gui_snapshot_rejects_nonpositive_limit(tmp_path: Path):
    project = _project_with_sources(tmp_path)
    with pytest.raises(ValueError, match="limit must be >= 1"):
        build_debug_gui_snapshot(project, limit=0)


def test_gui_cli_launches_read_only_viewer(tmp_path: Path, monkeypatch):
    project = _project_with_sources(tmp_path)
    captured = {}

    def fake_launch(project_arg, *, limit):
        captured["root"] = project_arg.root
        captured["limit"] = limit
        return 0

    monkeypatch.setattr("zddv.cli.launch_debug_gui", fake_launch)

    rc = main(["--project", str(project.root), "gui", "--limit", "7"])

    assert rc == 0
    assert captured == {"root": project.root, "limit": 7}

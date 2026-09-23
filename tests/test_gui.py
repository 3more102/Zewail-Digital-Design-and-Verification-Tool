from __future__ import annotations

from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.gui import build_debug_gui_snapshot
from zddv.storage import (
    record_coverage_score_snapshot,
    record_run,
)


def _run_record(run_id: str, status: str, *, seed: int, log: Path) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-23T06:{seed:02d}:00+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator 5.x",
        "top": "top",
        "test": "smoke",
        "seed": seed,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 10.0 + seed,
        "run_dir": str(log.parent),
        "log": str(log),
        "waveform": None,
        "coverage": None,
        "timeout_s": 30.0,
        "command": ["sim"],
        "plusargs": [],
    }


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "design.sv").write_text(
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
    return project


def test_debug_gui_snapshot_is_deterministic_read_only_evidence_view(tmp_path: Path):
    project = _project(tmp_path)
    pass_log = tmp_path / "pass.log"
    fail_log = tmp_path / "fail.log"
    pass_log.write_text("PASS\n", encoding="utf-8")
    fail_log.write_text(
        "ASSERT mismatch packet=41 expected=42 actual=43\n",
        encoding="utf-8",
    )

    record_run(project, _run_record("run-pass", "PASS", seed=1, log=pass_log))
    record_run(project, _run_record("run-fail", "FAIL", seed=2, log=fail_log))
    record_coverage_score_snapshot(
        project,
        {
            "snapshot_id": "cov-score",
            "created_at": "2026-09-23T06:20:00+00:00",
            "project": "demo",
            "simulator": "questa",
            "input_count": 2,
            "score": 93.5,
            "by_metric": {"line": 95.0, "branch": 92.0},
            "by_metric_counts": {
                "line": {"covered": 95, "total": 100},
                "branch": {"covered": 46, "total": 50},
            },
            "merged": ".zddv/coverage/coverage.ucdb",
            "summary": ".zddv/coverage/summary.txt",
            "metrics_path": ".zddv/coverage/metrics.json",
        },
    )

    first = build_debug_gui_snapshot(project, limit=10)
    second = build_debug_gui_snapshot(project, limit=10)

    assert first == second
    assert first["project"]["name"] == "demo"
    assert first["project"]["top"] == "top"
    assert first["stats"]["total"] == 2
    assert first["stats"]["passed"] == 1
    assert first["stats"]["failed"] == 1
    assert [row["run_id"] for row in first["runs"]] == ["run-fail", "run-pass"]
    assert first["failure_groups"][0]["signature"] == (
        "ASSERT mismatch packet=# expected=# actual=#"
    )
    assert first["latest_coverage"]["snapshot_id"] == "cov-score"
    assert first["latest_coverage"]["percent"] == 93.5
    assert first["design"]["summary"] == {
        "files": 1,
        "units": 2,
        "instances": 1,
        "duplicate_unit_names": 0,
    }
    assert first["design"]["hierarchy"]["children"][0]["path"] == "top.u_leaf"
    assert first["latest_formal"] is None
    assert first["latest_uvm"] is None
    assert first["policy"] == {
        "display_only": True,
        "executes_verification": False,
        "invokes_ai": False,
        "applies_generated_artifacts": False,
        "mutates_project_files": False,
    }


def test_debug_gui_snapshot_rejects_nonpositive_limit(tmp_path: Path):
    project = _project(tmp_path)
    with pytest.raises(ValueError, match="limit must be >= 1"):
        build_debug_gui_snapshot(project, limit=0)


def test_gui_cli_launches_viewer_with_requested_limit(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    captured = {}

    def fake_launch(project_arg, *, limit):
        captured["root"] = project_arg.root
        captured["limit"] = limit
        return 0

    monkeypatch.setattr("zddv.cli.launch_debug_gui", fake_launch)

    rc = main(["--project", str(project.root), "gui", "--limit", "7"])

    assert rc == 0
    assert captured == {"root": project.root, "limit": 7}

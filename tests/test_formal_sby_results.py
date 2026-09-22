from __future__ import annotations

from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.formal.sby_results import analyze_sby_log, parse_sby_log
from zddv.storage import list_formal_result_snapshots


_FAIL_LOG = """\
SBY [fifo_nofullskip] engine_0.basecase: ## Assert failed in fifo: a_count_diff
SBY [fifo_nofullskip] engine_0.basecase: ## Writing trace to VCD file: engine_0/trace.vcd
SBY [fifo_nofullskip] summary: engine_0 (smtbmc boolector) returned FAIL for basecase
SBY [fifo_nofullskip] summary: counterexample trace: fifo_nofullskip/engine_0/trace.vcd
SBY [fifo_nofullskip] DONE (FAIL, rc=2)
"""

_COVER_LOG = """\
SBY [fifo_cover] engine_0: ## Reached cover statement at w_underfill in step 2.
SBY [fifo_cover] engine_0: ## Writing trace to VCD file: engine_0/trace4.vcd
SBY [fifo_cover] summary: engine_0 (smtbmc boolector) returned PASS
SBY [fifo_cover] DONE (PASS, rc=0)
"""


def test_parse_sby_failure_keeps_explicit_assertion_and_trace(tmp_path: Path):
    run_dir = tmp_path / "fifo_nofullskip"
    run_dir.mkdir()
    log = run_dir / "logfile.txt"
    log.write_text(_FAIL_LOG, encoding="utf-8")

    result = parse_sby_log(log, mode="bmc", depth=20)

    assert result.backend == "sby"
    assert result.status == "FAIL"
    assert result.returncode == 2
    assert result.engine == "smtbmc boolector"
    assert result.request.mode == "bmc"
    assert result.request.depth == 20
    assert len(result.properties) == 1

    item = result.properties[0]
    assert item.name == "a_count_diff"
    assert item.kind == "assert"
    assert item.status == "FAIL"
    assert item.trace_path == (run_dir / "engine_0" / "trace.vcd").resolve()
    assert result.artifacts == (item.trace_path,)


def test_parse_sby_cover_keeps_reached_goal_without_inventing_unreached_goals(
    tmp_path: Path,
):
    run_dir = tmp_path / "fifo_cover"
    run_dir.mkdir()
    log = run_dir / "logfile.txt"
    log.write_text(_COVER_LOG, encoding="utf-8")

    result = parse_sby_log(log, mode="cover", depth=20)

    assert result.status == "PASS"
    assert len(result.properties) == 1
    item = result.properties[0]
    assert item.name == "w_underfill"
    assert item.kind == "cover"
    assert item.status == "COVERED"
    assert item.depth is None
    assert "solver step 2" in (item.message or "")
    assert item.trace_path == (run_dir / "engine_0" / "trace4.vcd").resolve()


def test_parse_sby_incomplete_log_refuses_to_infer_status(tmp_path: Path):
    log = tmp_path / "logfile.txt"
    log.write_text(
        "SBY [job] engine_0: ## Assert failed in top: p_bad\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no terminal DONE marker"):
        parse_sby_log(log, mode="bmc", depth=10)


def test_analyze_sby_log_persists_native_snapshot(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / "formal-run"
    run_dir.mkdir()
    log = run_dir / "logfile.txt"
    log.write_text(_FAIL_LOG, encoding="utf-8")

    record = analyze_sby_log(project, log, mode="bmc", depth=20)

    assert record["backend"] == "sby"
    assert record["input_format"] == "symbiyosys-log"
    assert record["summary"]["counterexamples"] == 1
    assert Path(record["report_path"]).is_file()

    rows = list_formal_result_snapshots(project, limit=10)
    assert len(rows) == 1
    assert rows[0]["status"] == "FAIL"
    assert rows[0]["mode"] == "bmc"
    assert rows[0]["counterexample_count"] == 1


def test_formal_sby_analyze_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "sby.log"
    log.write_text(_COVER_LOG, encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-sby-analyze",
            str(log),
            "--mode",
            "cover",
            "--depth",
            "20",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert "FORMAL SBY PASS" in output
    assert "mode=cover scope=COVER" in output
    assert "properties=1" in output
    assert "covered=1" in output

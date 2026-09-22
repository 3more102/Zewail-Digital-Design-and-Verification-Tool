from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage_holes
from zddv.config import ProjectConfig
from zddv.coverage import (
    build_coverage_hole_report,
    parse_vcs_urg_condition_coverage_points,
)


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="vcs",
        rtl=[],
        tb=[],
        waveform=False,
        coverage=True,
    )


def _condition_html() -> str:
    return """<html><body>
<h2>Cond Coverage for Instance : tb.dut.u_arbiter</h2>
<table>
<tr><th></th><th>Total</th><th>Covered</th><th>Percent</th></tr>
<tr><td>Conditions</td><td>6</td><td>2</td><td>33.33</td></tr>
</table>
<pre>
LINE       125
EXPRESSION (sram_rvalid_i & ((|steer)))
            ------1------   -----2----
</pre>
<table>
<tr><th>-1-</th><th>-2-</th><th>Status</th><th>Tests</th></tr>
<tr><td>0</td><td>1</td><td>Not Covered</td><td></td></tr>
<tr><td>1</td><td>0</td><td>Not Covered</td><td></td></tr>
<tr><td>1</td><td>1</td><td>Not Covered</td><td></td></tr>
</table>
<pre>
LINE       132
SUB-EXPRESSION (sram_req_o &amp;&amp; ((~sram_write_o)))
                -----1----    --------2--------
</pre>
<table>
<tr><th>-1-</th><th>-2-</th><th>Status</th><th>Tests</th></tr>
<tr><td>0</td><td>1</td><td>Covered</td><td>T1,T2</td></tr>
<tr><td>1</td><td>0</td><td>Covered</td><td>T3</td></tr>
<tr><td>1</td><td>1</td><td>Not Covered</td><td></td></tr>
</table>
</body></html>
"""


def test_parse_vcs_condition_points_normalizes_truth_rows_and_deduplicates(
    tmp_path: Path,
):
    report_dir = tmp_path / "urg-report"
    report_dir.mkdir()
    (report_dir / "mod0.html").write_text(_condition_html(), encoding="utf-8")

    # URG can paginate/repeat instance detail. Repeated evidence must not
    # double-count normalized truth-table points.
    (report_dir / "mod0_1.html").write_text(
        _condition_html(),
        encoding="utf-8",
    )

    points = parse_vcs_urg_condition_coverage_points(report_dir)

    assert len(points) == 6
    assert {point["scope"] for point in points} == {"tb.dut.u_arbiter"}
    assert {point["line"] for point in points} == {125, 132}
    assert sum(bool(point["hit"]) for point in points) == 2

    first = next(
        point
        for point in points
        if point["line"] == 125 and point["fec_target"] == "0 | 1"
    )
    assert first["type"] == "condition"
    assert first["condition"] == "(sram_rvalid_i & ((|steer)))"
    assert first["hit"] is False
    assert first["count"] == 0
    assert first["evidence"] == "URG status: Not Covered"

    second = next(
        point
        for point in points
        if point["line"] == 132 and point["fec_target"] == "1 | 0"
    )
    assert second["condition"] == "(sram_req_o && ((~sram_write_o)))"
    assert second["detail"] == "sub_expression"
    assert second["hit"] is True

    holes = build_coverage_hole_report(
        points,
        point_type="condition",
    )
    assert holes["total_holes"] == 4
    assert holes["by_type"] == {"condition": 4}
    assert {hole["line"] for hole in holes["holes"]} == {125, 132}


def test_parse_vcs_condition_points_rejects_conflicting_duplicate_status(
    tmp_path: Path,
):
    report_dir = tmp_path / "urg-report"
    report_dir.mkdir()
    (report_dir / "mod0.html").write_text(_condition_html(), encoding="utf-8")
    conflicting = _condition_html().replace(
        "<td>0</td><td>1</td><td>Not Covered</td>",
        "<td>0</td><td>1</td><td>Covered</td>",
        1,
    )
    (report_dir / "mod0_1.html").write_text(conflicting, encoding="utf-8")

    with pytest.raises(ValueError, match="Conflicting URG condition status"):
        parse_vcs_urg_condition_coverage_points(report_dir)


def test_cli_vcs_condition_holes_writes_shared_hole_schema(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    report_dir = project.root / ".zddv" / "coverage" / "urg-report"
    report_dir.mkdir(parents=True)
    (report_dir / "mod0.html").write_text(_condition_html(), encoding="utf-8")

    monkeypatch.setattr("zddv.cli.load_project", lambda _: project)
    args = SimpleNamespace(
        project=".",
        output=".zddv/coverage/holes.json",
        point_type="condition",
        limit=200,
        show=20,
    )

    assert cmd_coverage_holes(args) == 0

    output = project.root / ".zddv" / "coverage" / "holes.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["filter_type"] == "condition"
    assert payload["total_holes"] == 4
    assert payload["reported_holes"] == 4
    assert payload["by_type"] == {"condition": 4}
    assert all(item["scope"] == "tb.dut.u_arbiter" for item in payload["holes"])


def test_cli_vcs_condition_holes_rejects_unsupported_metric(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.cli.load_project", lambda _: project)
    args = SimpleNamespace(
        project=".",
        output=".zddv/coverage/holes.json",
        point_type="toggle",
        limit=200,
        show=20,
    )

    with pytest.raises(
        RuntimeError,
        match="VCS item-level coverage currently supports --type condition",
    ):
        cmd_coverage_holes(args)

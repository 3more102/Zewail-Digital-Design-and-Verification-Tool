import json
from pathlib import Path
from types import SimpleNamespace

from zddv.config import ProjectConfig
from zddv.coverage import (
    build_coverage_hole_report,
    load_normalized_coverage_points,
    merge_questa_coverage,
    parse_questa_code_coverage_xml,
    parse_questa_functional_coverage_report,
    parse_verilator_coverage,
    summarize_coverage_points,
    write_coverage_hole_report,
)
from zddv.storage import list_functional_coverage_snapshots


def test_parse_and_summarize_verilator_coverage(tmp_path: Path):
    coverage = tmp_path / "coverage.dat"
    coverage.write_text(
        "# SystemC::Coverage-3\n"
        "C 'fdesign.svl10n1pagev_line/top' 4\n"
        "C 'fdesign.svl11n1pagev_line/top' 0\n"
        "C 'fdesign.svl12n1pagev_toggle/top.sig' 7\n"
        "C 'fdesign.svl13n1pagev_user/top.cover' 0\n",
        encoding="utf-8",
    )

    points = parse_verilator_coverage(coverage)
    summary = summarize_coverage_points(points)

    assert len(points) == 4
    assert points[0]["type"] == "line"
    assert points[2]["type"] == "toggle"
    assert summary["total_points"] == 4
    assert summary["hit_points"] == 2
    assert summary["unhit_points"] == 2
    assert summary["hit_rate"] == 50.0
    assert summary["by_type"]["line"]["hit"] == 1
    assert summary["by_type"]["user"]["hit_rate"] == 0.0


def test_unknown_coverage_type_is_preserved(tmp_path: Path):
    coverage = tmp_path / "coverage.dat"
    coverage.write_text("C 'custom-key' 3\n", encoding="utf-8")

    points = parse_verilator_coverage(coverage)

    assert points == [
        {"name": "custom-key", "count": 3, "hit": True, "type": "unknown"}
    ]


def test_parse_questa_code_coverage_xml(tmp_path: Path):
    report = tmp_path / "questa-code.xml"
    report.write_text(
        """<?xml version="1.0"?>
<report lines="1" byInstance="1">
  <instance path="/tb/dut" du="dut">
    <source_table files="1">
      <file fn="0" path="rtl/dut.sv"></file>
    </source_table>
    <statement_data>
      <stmt fn="0" ln="10" st="1" hits="3"></stmt>
      <stmt fn="0" ln="11" st="1" hits="0"></stmt>
    </statement_data>
  </instance>
</report>
""",
        encoding="utf-8",
    )

    points = parse_questa_code_coverage_xml(report)

    assert len(points) == 2
    assert points[0]["type"] == "statement"
    assert points[0]["count"] == 3
    assert points[0]["hit"] is True
    assert points[0]["metadata"]["file"] == "rtl/dut.sv"
    assert points[0]["metadata"]["line"] == 10
    assert points[1]["count"] == 0
    assert points[1]["hit"] is False


def test_parse_questa_functional_coverage_report(tmp_path: Path):
    report = tmp_path / "questa-functional.txt"
    report.write_text(
        """COVERGROUP COVERAGE:
--------------------------------------------------------------------------------
 TYPE /alu_pkg/coverag/ALU_SIGNALS                     50.0%        100    Uncovered
    Coverpoint ALU_SIGNALS::RESET                      50.0%        100    Uncovered
        bin RESET_ACTIVE                                  14          1    Covered
        bin RESET_INACTIVE                                 0          1    ZERO
TOTAL COVERGROUP COVERAGE: 50.0%  COVERGROUP TYPES: 1
""",
        encoding="utf-8",
    )

    bins = parse_questa_functional_coverage_report(report)

    assert bins == [
        {
            "scope": "/alu_pkg/coverag/ALU_SIGNALS",
            "coverpoint": "ALU_SIGNALS::RESET",
            "bin": "RESET_ACTIVE",
            "hits": 14,
            "goal": 1,
            "metadata": {"questa_status": "Covered", "kind": "coverpoint"},
        },
        {
            "scope": "/alu_pkg/coverag/ALU_SIGNALS",
            "coverpoint": "ALU_SIGNALS::RESET",
            "bin": "RESET_INACTIVE",
            "hits": 0,
            "goal": 1,
            "metadata": {"questa_status": "ZERO", "kind": "coverpoint"},
        },
    ]


def test_merge_questa_coverage_normalizes_code_and_functional_bins(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "demo"
    run_root = root / ".zddv" / "runs"
    for run_id in ("run-a", "run-b"):
        run_dir = run_root / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "coverage.ucdb").write_text("ucdb fixture\n", encoding="utf-8")

    project = ProjectConfig(
        root=root,
        name="demo",
        simulator="questa",
        run_dir=".zddv/runs",
    )

    monkeypatch.setattr("zddv.coverage.shutil.which", lambda name: "vcover")
    commands: list[list[str]] = []

    def fake_run(command, cwd):
        commands.append(list(command))
        if len(command) > 1 and command[1] == "merge":
            Path(command[2]).write_text("merged ucdb\n", encoding="utf-8")
        elif len(command) > 1 and command[1] == "report":
            output = Path(command[command.index("-output") + 1])
            if "-xml" in command:
                output.write_text(
                    """<?xml version="1.0"?>
<report lines="1" byInstance="1">
  <instance path="/tb/dut" du="dut">
    <source_table files="1">
      <file fn="0" path="rtl/dut.sv"></file>
    </source_table>
    <statement_data>
      <stmt fn="0" ln="10" st="1" hits="3"></stmt>
      <stmt fn="0" ln="11" st="1" hits="0"></stmt>
    </statement_data>
  </instance>
</report>
""",
                    encoding="utf-8",
                )
            else:
                output.write_text(
                    """COVERGROUP COVERAGE:
 TYPE /tb/pkg/cg                                      50.0%        100    Uncovered
    Coverpoint cg::mode                               50.0%        100    Uncovered
        bin active                                       7          1    Covered
        bin idle                                         0          1    ZERO
TOTAL COVERGROUP COVERAGE: 50.0%  COVERGROUP TYPES: 1
""",
                    encoding="utf-8",
                )
        return SimpleNamespace(returncode=0, stdout="ok\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_questa_coverage(project)

    assert len(result["inputs"]) == 2
    assert Path(result["merged"]).name == "coverage.ucdb"
    assert Path(result["points_path"]).exists()
    assert Path(result["metrics_path"]).exists()
    assert result["metrics"]["total_points"] == 4
    assert result["metrics"]["hit_points"] == 2
    assert result["metrics"]["by_type"]["statement"]["total"] == 2
    assert result["metrics"]["by_type"]["covergroup_bin"]["total"] == 2
    assert result["functional_snapshot_id"] is not None

    points = load_normalized_coverage_points(project)
    assert len(points) == 4
    assert sum(point["hit"] for point in points) == 2

    fcov_rows = list_functional_coverage_snapshots(project, limit=10)
    assert len(fcov_rows) == 1
    assert fcov_rows[0]["source"] == "questa-ucdb"
    assert fcov_rows[0]["covered_bins"] == 1
    assert fcov_rows[0]["total_bins"] == 2

    assert commands[0][0:2] == ["vcover", "merge"]
    assert "-xml" in commands[1]
    assert "-codeAll" in commands[1]
    assert "-cvg" in commands[2]
    assert "-details" in commands[2]


def test_coverage_hole_report_filters_sorts_and_writes_json(tmp_path: Path):
    points = [
        {"name": "z-toggle", "count": 0, "hit": False, "type": "toggle"},
        {"name": "b-line", "count": 0, "hit": False, "type": "line"},
        {"name": "a-line", "count": 0, "hit": False, "type": "line"},
        {"name": "hit-line", "count": 2, "hit": True, "type": "line"},
    ]

    report = build_coverage_hole_report(points, limit=2)
    assert report["total_holes"] == 3
    assert report["reported_holes"] == 2
    assert report["by_type"] == {"line": 2, "toggle": 1}
    assert [hole["name"] for hole in report["holes"]] == ["a-line", "b-line"]

    filtered = build_coverage_hole_report(points, point_type="toggle")
    assert filtered["total_holes"] == 1
    assert filtered["holes"][0]["name"] == "z-toggle"

    output = tmp_path / "coverage" / "holes.json"
    written = write_coverage_hole_report(points, output, point_type="line")
    assert written["path"] == str(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["total_holes"] == 2
    assert payload["by_type"] == {"line": 2}

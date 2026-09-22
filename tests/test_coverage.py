import json
from pathlib import Path
from types import SimpleNamespace

from zddv.config import ProjectConfig
from zddv.coverage import (
    build_coverage_hole_report,
    parse_questa_coverage_summary,
    parse_questa_statement_coverage_xml,
    parse_verilator_coverage,
    summarize_coverage_points,
    write_coverage_hole_report,
    write_questa_statement_hole_report,
)


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

def test_parse_questa_coverage_summary_normalizes_vcover_table():
    text = """Coverage Report Totals BY INSTANCES: Number of Instances 23

    Enabled Coverage              Bins      Hits    Misses    Weight  Coverage
    ----------------              ----      ----    ------    ------  --------
    Branches                      3044      2982        62         1    97.96%
    Expressions                   1665      1143       522         1    68.64%
    Statements                    4920      4920         0         1   100.00%
    Toggles                      72906     37574     35332         1    51.53%
Total coverage (filtered view): 79.53%
"""

    summary = parse_questa_coverage_summary(text)

    assert summary["total_points"] == 82535
    assert summary["hit_points"] == 46619
    assert summary["unhit_points"] == 35916
    assert round(summary["hit_rate"], 3) == 56.484
    assert summary["by_type"]["statement"]["hit_rate"] == 100.0
    assert summary["by_type"]["toggle"]["total"] == 72906
    assert summary["tool_total_coverage"] == 79.53


def test_parse_questa_statement_xml_normalizes_zero_hit_locations(tmp_path: Path):
    xml_path = tmp_path / "coverage.xml"
    xml_path.write_text(
        """<?xml version="1.0"?>
<coverage_report>
  <code_coverage_report lines="1" byInstance="1">
    <instanceData path="/tb/dut" du="dut">
      <sourceTable files="1">
        <fileMap fn="0" path="rtl/dut.sv" />
      </sourceTable>
      <statements active="3" hits="1" percent="33.3" />
      <stmt fn="0" ln="10" st="1" hits="4" />
      <stmt fn="0" ln="12" st="1" hits="0" />
      <stmt fn="0" ln="12" st="2" hits="0" />
    </instanceData>
  </code_coverage_report>
</coverage_report>
""",
        encoding="utf-8",
    )

    points = parse_questa_statement_coverage_xml(xml_path)

    assert len(points) == 3
    assert points[0]["name"] == "/tb/dut|rtl/dut.sv:10:stmt1"
    assert points[0]["hit"] is True
    assert points[1]["file"] == "rtl/dut.sv"
    assert points[1]["line"] == 12
    assert points[1]["statement"] == 1
    assert points[1]["hit"] is False

    holes = build_coverage_hole_report(points, point_type="statement")
    assert holes["total_holes"] == 2
    assert holes["holes"][0]["scope"] == "/tb/dut"
    assert holes["holes"][0]["file"] == "rtl/dut.sv"
    assert holes["holes"][0]["line"] == 12


def test_parse_questa_statement_xml_handles_namespaced_legacy_shape(tmp_path: Path):
    xml_path = tmp_path / "coverage.xml"
    xml_path.write_text(
        """<?xml version="1.0"?>
<report xmlns="http://model.com/coverage" lines="1" byInstance="1">
  <instance path="/tb/dut" du="dut">
    <source_table files="1">
      <file fn="0" path="rtl/dut.sv"></file>
    </source_table>
    <statement_data>
      <stmt fn="0" ln="39" st="1" hits="0"></stmt>
    </statement_data>
  </instance>
</report>
""",
        encoding="utf-8",
    )

    points = parse_questa_statement_coverage_xml(xml_path)

    assert points == [
        {
            "type": "statement",
            "name": "/tb/dut|rtl/dut.sv:39:stmt1",
            "count": 0,
            "hit": False,
            "scope": "/tb/dut",
            "file": "rtl/dut.sv",
            "line": 39,
            "statement": 1,
        }
    ]


def test_write_questa_statement_hole_report_uses_merged_ucdb(
    tmp_path: Path,
    monkeypatch,
):
    project = ProjectConfig(
        root=tmp_path,
        name="demo",
        top="tb_top",
        simulator="questa",
        rtl=[],
        tb=[],
        waveform=False,
        coverage=True,
    )
    merged = tmp_path / ".zddv" / "coverage" / "coverage.ucdb"
    merged.parent.mkdir(parents=True)
    merged.write_text("ucdb fixture\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run(command, cwd):
        captured["command"] = list(command)
        captured["cwd"] = Path(cwd)
        xml_path = Path(command[command.index("-output") + 1])
        xml_path.write_text(
            """<?xml version="1.0"?>
<coverage_report>
  <code_coverage_report lines="1" byInstance="1">
    <instanceData path="/tb/dut" du="dut">
      <sourceTable files="1">
        <fileMap fn="0" path="rtl/dut.sv" />
      </sourceTable>
      <stmt fn="0" ln="20" st="1" hits="0" />
      <stmt fn="0" ln="21" st="1" hits="9" />
    </instanceData>
  </code_coverage_report>
</coverage_report>
""",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="report complete\n")

    monkeypatch.setattr("zddv.coverage.shutil.which", lambda name: "vcover")
    monkeypatch.setattr("zddv.coverage._run", fake_run)

    output = tmp_path / ".zddv" / "coverage" / "holes.json"
    report = write_questa_statement_hole_report(
        project,
        output,
        limit=20,
    )

    command = captured["command"]
    assert command[:6] == [
        "vcover",
        "report",
        "-xml",
        "-notimestamps",
        "-code",
        "s",
    ]
    assert command[-1] == str(merged.resolve())
    assert report["source"] == "questa-vcover-xml"
    assert report["total_holes"] == 1
    assert report["holes"][0]["name"] == "/tb/dut|rtl/dut.sv:20:stmt1"
    assert Path(report["xml"]).exists()
    assert Path(report["path"]) == output

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["by_type"] == {"statement": 1}
    assert payload["holes"][0]["line"] == 20


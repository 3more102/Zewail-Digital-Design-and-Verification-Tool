from pathlib import Path
import json
import sqlite3

from zddv.config import ProjectConfig
from zddv.design_index import index_project, parse_systemverilog


def test_parse_units_instances_and_comments(tmp_path: Path):
    source = tmp_path / "design.sv"
    source.write_text(
        """// module fake; endmodule
module leaf(input logic clk);
endmodule

module top(input logic clk);
  // leaf ignored(.clk(clk));
  leaf #(.W(8)) u_leaf (.clk(clk));
  external_ip u_ext (.clk(clk));
  string s = "leaf fake_string(.clk(clk));";
endmodule
""",
        encoding="utf-8",
    )

    units, instances = parse_systemverilog(source, root=tmp_path)

    assert [unit.name for unit in units] == ["leaf", "top"]
    assert [
        (item.parent, item.child_type, item.name)
        for item in instances
    ] == [
        ("top", "leaf", "u_leaf"),
        ("top", "external_ip", "u_ext"),
    ]


def test_index_project_writes_hierarchy_json_and_database(tmp_path: Path):
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()

    (root / "rtl" / "leaf.sv").write_text(
        "module leaf(input logic clk); endmodule\n",
        encoding="utf-8",
    )
    (root / "tb" / "tb_top.sv").write_text(
        "module tb_top; leaf dut(); missing_ip u_missing(); endmodule\n",
        encoding="utf-8",
    )

    project = ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
    )

    result = index_project(project)

    assert result["hierarchy"]["top_found"] is True
    assert {
        node["path"] for node in result["hierarchy"]["nodes"]
    } == {"tb_top", "tb_top.dut"}
    assert result["unresolved_instances"][0]["child_type"] == "missing_ip"

    json_path = Path(result["json_path"])
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "zddv.design-index.v1"

    with sqlite3.connect(result["database_path"]) as db:
        unit_count = db.execute(
            "SELECT COUNT(*) FROM design_units"
        ).fetchone()[0]
        edge_count = db.execute(
            "SELECT COUNT(*) FROM hierarchy_edges"
        ).fetchone()[0]
        unresolved = db.execute(
            "SELECT COUNT(*) FROM instances WHERE resolved = 0"
        ).fetchone()[0]

    assert unit_count == 2
    assert edge_count == 2
    assert unresolved == 1


def test_missing_top_does_not_fabricate_hierarchy(tmp_path: Path):
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "only.sv").write_text(
        "module only; endmodule\n",
        encoding="utf-8",
    )

    project = ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        rtl=["rtl/*.sv"],
    )
    result = index_project(project)

    assert result["hierarchy"]["top_found"] is False
    assert result["hierarchy"]["nodes"] == []
    assert result["hierarchy"]["edges"] == []

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.design_index import format_hierarchy_tree, index_project


def _project_with_sources(tmp_path: Path):
    root = tmp_path / "demo"
    project = initialize_project(root)
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.top = "tb_top"
    save_project(project)
    return project


def test_index_project_builds_source_index_and_hierarchy(tmp_path: Path):
    project = _project_with_sources(tmp_path)

    (project.root / "rtl" / "leaf.sv").write_text(
        """
module leaf(input logic clk);
endmodule

module mid(input logic clk);
    leaf u_leaf(.clk(clk));
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    (project.root / "tb" / "tb.sv").write_text(
        """
module tb_top;
    logic clk;
    mid u_mid(.clk(clk));
endmodule
""".lstrip(),
        encoding="utf-8",
    )

    result = index_project(project)

    assert Path(result["source_index_path"]).is_file()
    assert Path(result["hierarchy_path"]).is_file()

    source_index = json.loads(Path(result["source_index_path"]).read_text())
    hierarchy = json.loads(Path(result["hierarchy_path"]).read_text())

    assert source_index["stats"] == {"files": 2, "units": 3, "instances": 2}
    assert [unit["name"] for unit in source_index["units"]] == ["leaf", "mid", "tb_top"]

    root = hierarchy["root"]
    assert root["unit"] == "tb_top"
    assert root["children"][0]["instance"] == "u_mid"
    assert root["children"][0]["children"][0]["instance"] == "u_leaf"
    assert hierarchy["stats"]["reachable_instances"] == 2

    assert format_hierarchy_tree(root) == [
        "tb_top : tb_top",
        "└─ u_mid : mid",
        "   └─ u_leaf : leaf",
    ]


def test_index_ignores_comments_and_handles_parameterized_instance(tmp_path: Path):
    project = _project_with_sources(tmp_path)

    (project.root / "rtl" / "leaf.sv").write_text(
        "module leaf #(parameter int W = 4) (); endmodule\n",
        encoding="utf-8",
    )
    (project.root / "tb" / "tb.sv").write_text(
        """
module tb_top;
    // leaf fake();
    string message = "leaf also_fake()";
    leaf #(.W(8)) dut ();
endmodule
""".lstrip(),
        encoding="utf-8",
    )

    result = index_project(project)
    units = {unit["name"]: unit for unit in result["source_index"]["units"]}

    assert units["tb_top"]["instances"] == [
        {"unit": "leaf", "instance": "dut", "line": 4}
    ]


def test_index_reports_missing_top(tmp_path: Path):
    project = _project_with_sources(tmp_path)
    project.top = "missing_top"

    (project.root / "rtl" / "leaf.sv").write_text(
        "module leaf; endmodule\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Configured top 'missing_top'"):
        index_project(project)


def test_index_cli_command(tmp_path: Path, capsys):
    project = _project_with_sources(tmp_path)
    (project.root / "rtl" / "leaf.sv").write_text(
        "module leaf; endmodule\n",
        encoding="utf-8",
    )
    (project.root / "tb" / "tb.sv").write_text(
        "module tb_top; leaf dut(); endmodule\n",
        encoding="utf-8",
    )

    rc = main(["--project", str(project.root), "index"])

    assert rc == 0
    output = capsys.readouterr().out
    assert "INDEX: 2 file(s), 2 design unit(s), 1 instance declaration(s)" in output
    assert "tb_top : tb_top" in output
    assert "dut : leaf" in output

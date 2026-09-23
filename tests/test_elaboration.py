from __future__ import annotations

import json
from pathlib import Path

from zddv.config import initialize_project, save_project
from zddv.elaboration import (
    hierarchy_lines,
    parse_verilator_json,
    parse_verilator_xml,
    write_elaborated_index,
)
from zddv.simulator.verilator import VerilatorBackend


def test_parse_verilator_json_elaborated_hierarchy(tmp_path: Path):
    rtl = tmp_path / "rtl" / "counter.sv"
    tb = tmp_path / "tb" / "tb_counter.sv"
    rtl.parent.mkdir()
    tb.parent.mkdir()
    rtl.write_text("module counter; endmodule\n", encoding="utf-8")
    tb.write_text("module tb_counter; endmodule\n", encoding="utf-8")

    ast = {
        "type": "NETLIST",
        "nodes": [
            {
                "type": "MODULE",
                "name": "tb_counter",
                "origName": "tb_counter",
                "addr": "(A)",
                "loc": "c,1:1,10:1",
                "topModule": True,
            },
            {
                "type": "MODULE",
                "name": "counter",
                "origName": "counter",
                "addr": "(B)",
                "loc": "d,1:1,10:1",
            },
            {
                "type": "CELL",
                "name": "tb_counter.dut",
                "origName": "dut",
                "modp": "(B)",
                "loc": "c,5:3,5:10",
            },
        ],
    }
    meta = {
        "files": {
            "c": {"filename": "tb/tb_counter.sv", "realpath": str(tb)},
            "d": {"filename": "rtl/counter.sv", "realpath": str(rtl)},
        }
    }

    ast_path = tmp_path / "tree.json"
    meta_path = tmp_path / "tree.meta.json"
    ast_path.write_text(json.dumps(ast), encoding="utf-8")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    result = parse_verilator_json(
        ast_path,
        meta_path,
        project_root=tmp_path,
        top="tb_counter",
    )

    assert [module["name"] for module in result["modules"]] == [
        "tb_counter",
        "counter",
    ]
    assert result["modules"][1]["location"]["path"] == "rtl/counter.sv"
    assert result["instances"][1]["path"] == "tb_counter.dut"
    assert result["instances"][1]["module"] == "counter"
    assert result["instances"][1]["location"]["line"] == 5


def test_parse_verilator_json_preserves_generated_scope_paths(tmp_path: Path):
    rtl = tmp_path / "rtl" / "design.sv"
    rtl.parent.mkdir()
    rtl.write_text(
        "module leaf; endmodule\\n"
        "module top; genvar i; for (i = 0; i < 2; i++) begin: g "
        "leaf u_leaf(); end endmodule\\n",
        encoding="utf-8",
    )

    ast = {
        "type": "NETLIST",
        "modulesp": [
            {
                "type": "MODULE",
                "name": "top",
                "origName": "top",
                "verilogName": "top",
                "addr": "(A)",
                "level": 1,
                "loc": "d,2:8,2:11",
                "stmtsp": [
                    {
                        "type": "GENBLOCK",
                        "name": "g[0]",
                        "itemsp": [
                            {
                                "type": "CELL",
                                "name": "u_leaf",
                                "origName": "u_leaf",
                                "verilogName": "u_leaf",
                                "modName": "leaf",
                                "modp": "(B)",
                                "loc": "d,2:47,2:53",
                            }
                        ],
                    },
                    {
                        "type": "GENBLOCK",
                        "name": "g[1]",
                        "itemsp": [
                            {
                                "type": "CELL",
                                "name": "u_leaf",
                                "origName": "u_leaf",
                                "verilogName": "u_leaf",
                                "modName": "leaf",
                                "modp": "(B)",
                                "loc": "d,2:47,2:53",
                            }
                        ],
                    },
                ],
            },
            {
                "type": "MODULE",
                "name": "leaf",
                "origName": "leaf",
                "verilogName": "leaf",
                "addr": "(B)",
                "level": 2,
                "loc": "d,1:8,1:12",
            },
        ],
    }
    meta = {
        "files": {
            "d": {
                "filename": "rtl/design.sv",
                "realpath": str(rtl),
                "language": "1800-2023",
            }
        }
    }
    ast_path = tmp_path / "tree.json"
    meta_path = tmp_path / "tree.meta.json"
    ast_path.write_text(json.dumps(ast), encoding="utf-8")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    result = parse_verilator_json(
        ast_path,
        meta_path,
        project_root=tmp_path,
        top="top",
    )

    assert [item["path"] for item in result["instances"]] == [
        "top",
        "top.g[0].u_leaf",
        "top.g[1].u_leaf",
    ]
    assert result["instances"][1]["module"] == "leaf"
    assert result["instances"][1]["generate_scopes"] == ["g[0]"]
    assert result["instances"][2]["generate_scopes"] == ["g[1]"]


def test_parse_legacy_verilator_xml_elaborated_hierarchy(tmp_path: Path):
    xml_path = tmp_path / "tree.xml"
    xml_path.write_text(
        """<verilator_xml>
<files>
  <file id="a" filename="tb/tb_counter.sv"/>
  <file id="b" filename="rtl/counter.sv"/>
</files>
<cells>
  <cell loc="a,1,1,1,10" name="tb_counter" submodname="tb_counter" hier="tb_counter">
    <cell loc="a,5,5,3,10" name="dut" submodname="counter" hier="tb_counter.dut"/>
  </cell>
</cells>
<netlist>
  <module loc="a,1,10,1,1" name="tb_counter" origName="tb_counter" topModule="1"/>
  <module loc="b,1,10,1,1" name="counter" origName="counter"/>
</netlist>
</verilator_xml>
""",
        encoding="utf-8",
    )

    result = parse_verilator_xml(
        xml_path,
        project_root=tmp_path,
        top="tb_counter",
    )

    assert len(result["modules"]) == 2
    assert [item["path"] for item in result["instances"]] == [
        "tb_counter",
        "tb_counter.dut",
    ]
    assert result["instances"][1]["module"] == "counter"
    assert result["instances"][1]["location"]["column"] == 3


def test_elaborated_hierarchy_lines_and_version_detection():
    index = {
        "top": "tb_top",
        "instances": [
            {"path": "tb_top", "name": "tb_top", "module": "tb_top"},
            {"path": "tb_top.dut", "name": "dut", "module": "core"},
            {"path": "tb_top.dut.u_alu", "name": "u_alu", "module": "alu"},
        ],
    }

    assert hierarchy_lines(index) == [
        "tb_top: tb_top",
        "  dut: core",
        "    u_alu: alu",
    ]
    assert VerilatorBackend._version_tuple("Verilator 5.020 2024-01-01") == (5, 20)
    assert VerilatorBackend._version_tuple("Verilator 5.052 devel") == (5, 52)
    assert VerilatorBackend._version_tuple("Verilator 5.043") < (5, 44)
    assert VerilatorBackend._version_tuple("Verilator 5.044") >= (5, 44)


class _FakeBackend:
    def export_design_tree(self, project, output_dir):
        ast_path = output_dir / "fake.tree.json"
        meta_path = output_dir / "fake.tree.meta.json"
        ast_path.write_text(
            json.dumps(
                {
                    "nodes": [
                        {
                            "type": "MODULE",
                            "name": "top",
                            "origName": "top",
                            "addr": "(A)",
                            "topModule": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        meta_path.write_text(json.dumps({"files": {}}), encoding="utf-8")
        return {
            "format": "json",
            "ast": str(ast_path),
            "meta": str(meta_path),
            "log": str(output_dir / "fake.log"),
            "command": ["verilator"],
            "simulator_version": "Verilator test",
        }


def test_write_elaborated_index_links_source_index(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "rtl" / "top.sv"
    source.write_text("module top; endmodule\n", encoding="utf-8")
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)

    result = write_elaborated_index(project, backend=_FakeBackend())

    assert Path(result["path"]).exists()
    assert Path(result["source_index"]).exists()
    assert Path(result["hierarchy_path"]).read_text(encoding="utf-8") == "top: top\n"
    assert result["summary"] == {"modules": 1, "instances": 1}

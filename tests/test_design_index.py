from __future__ import annotations

import json
from pathlib import Path

from zddv.design_index import (
    format_hierarchy,
    parse_verilator_json,
    parse_verilator_xml,
)
from zddv.simulator.verilator import VerilatorBackend


def test_parse_verilator_json_design_tree(tmp_path: Path):
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

    assert [module["name"] for module in result["modules"]] == ["tb_counter", "counter"]
    assert result["modules"][1]["location"]["path"] == "rtl/counter.sv"
    assert result["instances"][1]["hierarchy"] == "tb_counter.dut"
    assert result["instances"][1]["module"] == "counter"
    assert result["instances"][1]["location"]["line"] == 5


def test_parse_legacy_verilator_xml_design_tree(tmp_path: Path):
    xml_path = tmp_path / "tree.xml"
    xml_path.write_text(
        """<verilator_xml>
<files>
  <file id="a" filename="tb/tb_counter.sv"/>
  <file id="b" filename="rtl/counter.sv"/>
</files>
<cells>
  <cell loc="a,1,1,1,10" name="tb_counter" submodname="tb_counter" hier="tb_counter"/>
  <cell loc="a,5,5,3,10" name="dut" submodname="counter" hier="tb_counter.dut"/>
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
    assert [item["hierarchy"] for item in result["instances"]] == [
        "tb_counter",
        "tb_counter.dut",
    ]
    assert result["instances"][1]["module"] == "counter"


def test_hierarchy_formatting_and_version_detection():
    index = {
        "top": "tb_top",
        "instances": [
            {"hierarchy": "tb_top", "name": "tb_top", "module": "tb_top"},
            {"hierarchy": "tb_top.dut", "name": "dut", "module": "core"},
            {"hierarchy": "tb_top.dut.u_alu", "name": "u_alu", "module": "alu"},
        ],
    }

    assert format_hierarchy(index) == [
        "tb_top (tb_top)",
        "  dut (core)",
        "    u_alu (alu)",
    ]
    assert format_hierarchy(index, max_depth=1) == [
        "tb_top (tb_top)",
        "  dut (core)",
    ]
    assert VerilatorBackend._version_tuple("Verilator 5.020 2024-01-01") == (5, 20)
    assert VerilatorBackend._version_tuple("Verilator 5.052 devel") == (5, 52)

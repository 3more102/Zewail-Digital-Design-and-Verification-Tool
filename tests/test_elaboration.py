from __future__ import annotations

import json
import subprocess
from pathlib import Path

from zddv.config import initialize_project, save_project
from zddv.design_revision import design_revision_fingerprint
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


def test_parse_verilator_json_normalizes_documented_module_io_direction(tmp_path: Path):
    rtl = tmp_path / "rtl" / "ports.sv"
    rtl.parent.mkdir()
    rtl.write_text(
        "module top(input logic clk, output logic done); endmodule\n",
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
                "topModule": True,
                "loc": "d,1:1,1:55",
                "stmtsp": [
                    {
                        "type": "VAR",
                        "name": "clk",
                        "origName": "clk",
                        "verilogName": "clk",
                        "ioDirection": "INPUT",
                        "varType": "PORT",
                        "loc": "d,1:12,1:27",
                    },
                    {
                        "type": "VAR",
                        "name": "done",
                        "origName": "done",
                        "verilogName": "done",
                        "ioDirection": "OUTPUT",
                        "varType": "PORT",
                        "loc": "d,1:29,1:46",
                    },
                    {
                        "type": "VAR",
                        "name": "internal",
                        "ioDirection": "NONE",
                        "varType": "VAR",
                        "loc": "d,1:47,1:54",
                    },
                    {
                        "type": "VAR",
                        "name": "undocumented_direction_only",
                        "direction": "INPUT",
                        "declDirection": "INPUT",
                        "varType": "PORT",
                        "loc": "d,1:47,1:54",
                    },
                    {
                        "type": "TASK",
                        "name": "helper",
                        "stmtsp": [
                            {
                                "type": "VAR",
                                "name": "arg",
                                "verilogName": "arg",
                                "ioDirection": "INPUT",
                                "varType": "PORT",
                                "loc": "d,1:47,1:50",
                            }
                        ],
                    },
                ],
            }
        ],
    }
    meta = {
        "files": {
            "d": {
                "filename": "rtl/ports.sv",
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

    assert result["port_evidence"] == {
        "status": "NORMALIZED",
        "source_format": "json",
        "contract": "verilator_module_var_io_direction",
    }
    assert [(item["name"], item["direction"]) for item in result["ports"]] == [
        ("clk", "input"),
        ("done", "output"),
    ]
    assert all(item["direction_field"] == "ioDirection" for item in result["ports"])
    assert result["ports"][0]["module"] == "top"
    assert result["ports"][0]["location"]["path"] == "rtl/ports.sv"
    assert all(item["name"] != "arg" for item in result["ports"])
    assert all(
        item["name"] != "undocumented_direction_only"
        for item in result["ports"]
    )


def test_parse_verilator_json_normalizes_direct_cell_pin_varrefs_only(
    tmp_path: Path,
):
    rtl = tmp_path / "rtl" / "pins.sv"
    rtl.parent.mkdir()
    rtl.write_text(
        "module leaf(input logic a, input logic b); endmodule\n"
        "module top(input logic src, input logic other); "
        "leaf u_leaf(.a(src), .b(src & other)); endmodule\n",
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
                "topModule": True,
                "loc": "d,2:8,2:75",
                "stmtsp": [
                    {
                        "type": "CELL",
                        "name": "u_leaf",
                        "origName": "u_leaf",
                        "verilogName": "u_leaf",
                        "modName": "leaf",
                        "modp": "(B)",
                        "loc": "d,2:46,2:52",
                        "pinsp": [
                            {
                                "type": "PIN",
                                "name": "a",
                                "origName": "a",
                                "loc": "d,2:54,2:60",
                                "exprp": [
                                    {
                                        "type": "VARREF",
                                        "name": "src",
                                        "origName": "src",
                                        "verilogName": "src",
                                        "loc": "d,2:57,2:59",
                                    }
                                ],
                            },
                            {
                                "type": "PIN",
                                "name": "b",
                                "origName": "b",
                                "loc": "d,2:63,2:74",
                                "exprp": [
                                    {
                                        "type": "AND",
                                        "lhsp": {
                                            "type": "VARREF",
                                            "name": "src",
                                        },
                                        "rhsp": {
                                            "type": "VARREF",
                                            "name": "other",
                                        },
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
            {
                "type": "MODULE",
                "name": "leaf",
                "origName": "leaf",
                "verilogName": "leaf",
                "addr": "(B)",
                "level": 2,
                "loc": "d,1:8,1:47",
            },
        ],
    }
    meta = {
        "files": {
            "d": {
                "filename": "rtl/pins.sv",
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

    assert result["pin_binding_evidence"] == {
        "status": "NORMALIZED",
        "source_format": "json",
        "contract": "verilator_cell_pin_direct_varref_only",
        "unsupported_expression_count": 1,
    }
    assert len(result["pin_bindings"]) == 2
    simple = result["pin_bindings"][0]
    assert simple["instance_path"] == "top.u_leaf"
    assert simple["parent_instance_path"] == "top"
    assert simple["instance_module"] == "leaf"
    assert simple["pin"] == "a"
    assert simple["status"] == "NORMALIZED"
    assert simple["expression_type"] == "VARREF"
    assert simple["signal"] == "src"
    assert simple["signal_location"]["path"] == "rtl/pins.sv"

    complex_expr = result["pin_bindings"][1]
    assert complex_expr["pin"] == "b"
    assert complex_expr["status"] == "UNSUPPORTED"
    assert complex_expr["expression_type"] == "AND"
    assert complex_expr["signal"] is None


def test_parse_verilator_json_normalizes_module_root_direct_assignw_varrefs_only(
    tmp_path: Path,
):
    rtl = tmp_path / "rtl" / "assigns.sv"
    rtl.parent.mkdir()
    rtl.write_text(
        "module top(input logic src, input logic other, "
        "output logic dst, output logic complex_dst);\n"
        "assign dst = src;\n"
        "assign complex_dst = src & other;\n"
        "endmodule\n",
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
                "topModule": True,
                "loc": "d,1:1,4:9",
                "stmtsp": [
                    {
                        "type": "ASSIGNW",
                        "loc": "d,2:1,2:17",
                        "rhsp": [
                            {
                                "type": "VARREF",
                                "name": "src",
                                "origName": "src",
                                "verilogName": "src",
                                "loc": "d,2:14,2:16",
                            }
                        ],
                        "lhsp": [
                            {
                                "type": "VARREF",
                                "name": "dst",
                                "origName": "dst",
                                "verilogName": "dst",
                                "loc": "d,2:8,2:10",
                            }
                        ],
                    },
                    {
                        "type": "ASSIGNW",
                        "loc": "d,3:1,3:33",
                        "rhsp": [
                            {
                                "type": "AND",
                                "lhsp": {
                                    "type": "VARREF",
                                    "name": "src",
                                },
                                "rhsp": {
                                    "type": "VARREF",
                                    "name": "other",
                                },
                            }
                        ],
                        "lhsp": [
                            {
                                "type": "VARREF",
                                "name": "complex_dst",
                                "origName": "complex_dst",
                                "verilogName": "complex_dst",
                                "loc": "d,3:8,3:18",
                            }
                        ],
                    },
                ],
            }
        ],
    }
    meta = {
        "files": {
            "d": {
                "filename": "rtl/assigns.sv",
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

    assert result["direct_assignment_evidence"] == {
        "status": "NORMALIZED",
        "source_format": "json",
        "contract": "verilator_module_root_assignw_direct_varref_only",
        "unsupported_assignment_count": 1,
    }
    assert len(result["direct_assignments"]) == 2

    simple = result["direct_assignments"][0]
    assert simple["status"] == "NORMALIZED"
    assert simple["module"] == "top"
    assert simple["assignment_type"] == "ASSIGNW"
    assert simple["lhs_signal"] == "dst"
    assert simple["rhs_signal"] == "src"
    assert simple["lhs_aliases"] == ["dst"]
    assert simple["rhs_aliases"] == ["src"]
    assert simple["lhs_varrefs"] == ["dst"]
    assert simple["rhs_varrefs"] == ["src"]
    assert simple["location"]["path"] == "rtl/assigns.sv"
    assert simple["location"]["line"] == 2

    complex_expr = result["direct_assignments"][1]
    assert complex_expr["status"] == "UNSUPPORTED"
    assert complex_expr["lhs_expression_type"] == "VARREF"
    assert complex_expr["rhs_expression_type"] == "AND"
    assert complex_expr["lhs_signal"] is None
    assert complex_expr["rhs_signal"] is None
    assert complex_expr["lhs_varrefs"] == ["complex_dst"]
    assert complex_expr["rhs_varrefs"] == ["other", "src"]


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
    assert result["ports"] == []
    assert result["port_evidence"] == {
        "status": "UNAVAILABLE",
        "source_format": "xml",
        "reason": "legacy_xml_port_schema_not_normalized",
    }
    assert result["pin_bindings"] == []
    assert result["pin_binding_evidence"] == {
        "status": "UNAVAILABLE",
        "source_format": "xml",
        "reason": "legacy_xml_pin_binding_schema_not_normalized",
    }
    assert result["direct_assignments"] == []
    assert result["direct_assignment_evidence"] == {
        "status": "UNAVAILABLE",
        "source_format": "xml",
        "reason": "legacy_xml_direct_assignment_schema_not_normalized",
    }


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
    assert VerilatorBackend._version_tuple("Verilator 5.021") < (5, 22)
    assert VerilatorBackend._version_tuple("Verilator 5.022") >= (5, 22)


def test_verilator_export_uses_documented_json_version_boundary(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "version-boundary")
    source = project.root / "rtl" / "top.sv"
    source.write_text("module top; endmodule\\n", encoding="utf-8")
    project.rtl = ["rtl/*.sv"]
    project.tb = []
    project.top = "top"
    save_project(project)

    for version, expected_format, expected_flag in (
        ("Verilator 5.021", "xml", "--xml-only"),
        ("Verilator 5.022", "json", "--json-only"),
    ):
        backend = VerilatorBackend()
        monkeypatch.setattr(backend, "_tool", lambda: "verilator")
        monkeypatch.setattr(backend, "version", lambda version=version: version)

        seen: list[str] = []

        def fake_run(command, **kwargs):
            seen[:] = command
            if "--json-only-output" in command:
                ast_path = Path(command[command.index("--json-only-output") + 1])
                meta_path = Path(
                    command[command.index("--json-only-meta-output") + 1]
                )
                ast_path.write_text('{"type":"NETLIST","modulesp":[]}', encoding="utf-8")
                meta_path.write_text('{"files":{}}', encoding="utf-8")
            else:
                ast_path = Path(command[command.index("--xml-output") + 1])
                ast_path.write_text("<verilator_xml/>", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="")

        monkeypatch.setattr(
            "zddv.simulator.verilator.subprocess.run",
            fake_run,
        )
        result = backend.export_design_tree(
            project,
            project.root / ".zddv" / expected_format,
        )
        assert result["format"] == expected_format
        assert expected_flag in seen


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
    assert result["summary"] == {
        "modules": 1,
        "instances": 1,
        "ports": 0,
        "pin_bindings": 0,
    }
    assert result["ports"] == []
    assert result["port_evidence"] == {
        "status": "NORMALIZED",
        "source_format": "json",
        "contract": "verilator_module_var_io_direction",
    }
    assert result["pin_bindings"] == []
    assert result["pin_binding_evidence"] == {
        "status": "NORMALIZED",
        "source_format": "json",
        "contract": "verilator_cell_pin_direct_varref_only",
        "unsupported_expression_count": 0,
    }
    assert result["design_fingerprint"] == design_revision_fingerprint(project)

import json
from pathlib import Path
import subprocess

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.crossprobe import build_crossprobe, write_crossprobe_report
from zddv.design_index import build_design_index
from zddv.design_revision import design_revision_fingerprint
from zddv.waveform import build_waveform_index


VCD = """$timescale 1ns $end
$scope module TOP $end
$scope module tb_top $end
$var wire 1 ! clk $end
$scope module dut $end
$var wire 4 # count [3:0] $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 #
"""


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    rtl = project.root / "rtl"
    tb = project.root / "tb"
    rtl.mkdir(exist_ok=True)
    tb.mkdir(exist_ok=True)

    (rtl / "counter.sv").write_text(
        """module counter(
    input logic clk,
    output logic [3:0] count
);
    always_ff @(posedge clk)
        count <= count + 1'b1;
endmodule
""",
        encoding="utf-8",
    )
    (tb / "tb_top.sv").write_text(
        """module tb_top;
    logic clk;
    logic [3:0] count;
    counter dut(
        .clk(clk),
        .count(count)
    );
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.top = "tb_top"
    save_project(project)
    return project


def test_crossprobe_maps_vcd_scope_to_rtl_declaration(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    result = build_crossprobe(
        project,
        "tb_top.dut.count",
        build_waveform_index(waveform_path, project_name=project.name),
        design_index=build_design_index(project),
    )

    assert result["status"] == "MATCHED"
    assert result["signal_match"] == "path-suffix"
    assert result["waveform"]["signal"]["path"] == "TOP.tb_top.dut.count"
    assert result["hierarchy"]["design_path"] == "tb_top.dut"
    assert result["hierarchy"]["type"] == "counter"
    assert result["source"]["file"] == "rtl/counter.sv"
    assert result["source"]["declaration"]["line"] == 3
    assert "output logic [3:0] count" in result["source"]["declaration"]["text"]
    assert result["connectivity"]["analysis_level"] == "source_structural"
    assert result["connectivity"]["unit"] == "counter"
    assert result["connectivity"]["signal"] == "count"
    assert {item["kind"] for item in result["connectivity"]["drivers"]} == {"procedural_assignment"}
    assert {item["kind"] for item in result["connectivity"]["loads"]} == {"boundary_port", "procedural_assignment"}


def test_crossprobe_rejects_ambiguous_short_signal_name(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "ambiguous.vcd"
    waveform_path.write_text(
        VCD.replace(
            "$upscope $end\n$upscope $end\n$upscope $end",
            "$upscope $end\n$scope module dut2 $end\n"
            "$var wire 4 $ count [3:0] $end\n"
            "$upscope $end\n$upscope $end\n$upscope $end",
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="ambiguous"):
        build_crossprobe(
            project,
            "count",
            build_waveform_index(waveform_path, project_name=project.name),
            design_index=build_design_index(project),
        )


def test_crossprobe_cli_writes_report(tmp_path: Path, capsys):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    rc = main([
        "--project",
        str(project.root),
        "crossprobe",
        "tb_top.dut.count",
        "--input",
        "trace.vcd",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "CROSSPROBE MATCHED" in output
    assert "tb_top.dut" in output
    assert "rtl/counter.sv:3" in output
    assert "Connectivity: drivers=1 loads=2" in output
    assert (project.root / ".zddv" / "design" / "connectivity.json").is_file()
    assert (project.root / ".zddv" / "debug" / "crossprobe.json").is_file()


def test_write_crossprobe_report_can_use_direct_input(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    result = write_crossprobe_report(
        project,
        "TOP.tb_top.clk",
        input_path="trace.vcd",
    )

    assert result["status"] == "MATCHED"
    assert result["hierarchy"]["design_path"] == "tb_top"
    assert result["source"]["file"] == "tb/tb_top.sv"
    assert result["connectivity"]["unit"] == "tb_top"
    assert result["connectivity_index_path"].endswith("connectivity.json")
    assert Path(result["report_path"]).is_file()


def test_crossprobe_prefers_elaborated_generated_scope(tmp_path: Path):
    project = initialize_project(tmp_path / "generated")
    rtl = project.root / "rtl"
    tb = project.root / "tb"
    rtl.mkdir(exist_ok=True)
    tb.mkdir(exist_ok=True)

    (rtl / "leaf.sv").write_text(
        """module leaf(
    input logic clk,
    output logic count
);
    always_ff @(posedge clk)
        count <= ~count;
endmodule
""",
        encoding="utf-8",
    )
    (tb / "tb_top.sv").write_text(
        """module tb_top;
    logic clk;
    logic count;
    for (genvar i = 0; i < 2; i++) begin : g
        leaf u_leaf(.clk(clk), .count(count));
    end
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.top = "tb_top"
    save_project(project)

    waveform_path = project.root / "generated.vcd"
    waveform_path.write_text(
        """$timescale 1ns $end
$scope module TOP $end
$scope module tb_top $end
$scope module g[0] $end
$scope module u_leaf $end
$var wire 1 ! count $end
$upscope $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
""",
        encoding="utf-8",
    )
    elaborated = {
        "schema_version": 1,
        "project": project.name,
        "top": project.top,
        "simulator": project.simulator,
        "simulator_version": "Verilator test",
        "source_format": "json",
        "port_evidence": {
            "status": "NORMALIZED",
            "source_format": "json",
            "contract": "verilator_module_var_io_direction",
        },
        "ports": [
            {
                "module": "leaf",
                "module_elaborated_name": "leaf",
                "name": "count",
                "elaborated_name": "count",
                "verilog_name": "count",
                "original_name": "count",
                "direction": "output",
                "direction_raw": "OUTPUT",
                "direction_field": "ioDirection",
                "var_type": "PORT",
                "location": {"path": "rtl/leaf.sv", "line": 3, "column": 18},
            }
        ],
        "instances": [
            {
                "path": "tb_top",
                "name": "tb_top",
                "module": "tb_top",
                "top": True,
                "location": {"path": "tb/tb_top.sv", "line": 1},
            },
            {
                "path": "tb_top.g[0].u_leaf",
                "name": "u_leaf",
                "module": "leaf",
                "top": False,
                "generate_scopes": ["g[0]"],
                "location": {"path": "tb/tb_top.sv", "line": 5},
            },
        ],
    }

    result = build_crossprobe(
        project,
        "tb_top.g[0].u_leaf.count",
        build_waveform_index(waveform_path, project_name=project.name),
        design_index=build_design_index(project),
        elaborated_index=elaborated,
    )

    assert result["status"] == "MATCHED"
    assert result["hierarchy_resolution"] == "simulator_elaborated"
    assert result["hierarchy"]["design_path"] == "tb_top.g[0].u_leaf"
    assert result["hierarchy"]["type"] == "leaf"
    assert result["hierarchy"]["generate_scopes"] == ["g[0]"]
    assert result["elaborated_hierarchy"]["match"] == "scope-suffix"
    assert result["elaborated_port"]["status"] == "MATCHED"
    assert result["elaborated_port"]["instance_path"] == "tb_top.g[0].u_leaf"
    assert result["elaborated_port"]["module"] == "leaf"
    assert result["elaborated_port"]["signal"] == "count"
    assert result["elaborated_port"]["port"]["direction"] == "output"
    assert result["elaborated_port"]["port"]["direction_field"] == "ioDirection"
    assert result["source"]["unit"] == "leaf"
    assert result["source"]["file"] == "rtl/leaf.sv"
    assert result["source"]["declaration"]["line"] == 3
    assert result["connectivity"]["analysis_level"] == "source_structural"
    assert result["connectivity"]["unit"] == "leaf"
    assert result["connectivity"]["instance_path"] == "tb_top.g[0].u_leaf"
    assert (
        result["connectivity"]["instance_qualification"]
        == "simulator_elaborated_scope"
    )
    assert all(
        item["instance_path"] == "tb_top.g[0].u_leaf"
        for item in (
            result["connectivity"]["drivers"]
            + result["connectivity"]["loads"]
        )
    )


def test_crossprobe_preserves_unavailable_legacy_port_evidence(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    elaborated = {
        "schema_version": 1,
        "project": project.name,
        "top": project.top,
        "simulator": project.simulator,
        "simulator_version": "Verilator legacy XML",
        "source_format": "xml",
        "port_evidence": {
            "status": "UNAVAILABLE",
            "source_format": "xml",
            "reason": "legacy_xml_port_schema_not_normalized",
        },
        "ports": [],
        "instances": [
            {
                "path": "tb_top.dut",
                "name": "dut",
                "module": "counter",
                "top": False,
                "location": {"path": "rtl/counter.sv", "line": 1},
            }
        ],
    }

    result = build_crossprobe(
        project,
        "tb_top.dut.count",
        build_waveform_index(waveform_path, project_name=project.name),
        design_index=build_design_index(project),
        elaborated_index=elaborated,
    )

    assert result["status"] == "MATCHED"
    assert result["hierarchy_resolution"] == "simulator_elaborated"
    assert result["elaborated_port"] == {
        "status": "UNAVAILABLE",
        "source_format": "xml",
        "reason": "legacy_xml_port_schema_not_normalized",
    }


def test_crossprobe_exposes_direct_elaborated_pin_connectivity(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    elaborated = {
        "schema_version": 1,
        "project": project.name,
        "top": project.top,
        "simulator": project.simulator,
        "instances": [
            {
                "path": "tb_top",
                "name": "tb_top",
                "module": "tb_top",
                "top": True,
                "location": {"path": "tb/tb_top.sv", "line": 1},
            },
            {
                "path": "tb_top.dut",
                "name": "dut",
                "module": "counter",
                "top": False,
                "location": {"path": "tb/tb_top.sv", "line": 4},
            },
        ],
        "ports": [
            {
                "module": "counter",
                "name": "clk",
                "direction": "input",
            },
            {
                "module": "counter",
                "name": "count",
                "direction": "output",
            },
        ],
        "port_evidence": {
            "status": "NORMALIZED",
            "source_format": "json",
            "contract": "verilator_module_var_io_direction",
        },
        "pin_bindings": [
            {
                "status": "NORMALIZED",
                "instance_path": "tb_top.dut",
                "instance_module": "counter",
                "pin": "clk",
                "parent_instance_path": "tb_top",
                "signal": "clk",
                "generate_scopes": [],
            },
            {
                "status": "NORMALIZED",
                "instance_path": "tb_top.dut",
                "instance_module": "counter",
                "pin": "count",
                "parent_instance_path": "tb_top",
                "signal": "count",
                "generate_scopes": [],
            },
        ],
        "pin_binding_evidence": {
            "status": "NORMALIZED",
            "source_format": "json",
            "contract": "verilator_cell_pin_direct_varref_only",
            "unsupported_expression_count": 0,
        },
    }
    waveform = build_waveform_index(waveform_path, project_name=project.name)
    design = build_design_index(project)

    parent = build_crossprobe(
        project,
        "TOP.tb_top.clk",
        waveform,
        design_index=design,
        elaborated_index=elaborated,
    )
    parent_connectivity = parent["elaborated_connectivity"]
    assert parent_connectivity["analysis_level"] == (
        "simulator_elaborated_direct_pin_varref"
    )
    assert parent_connectivity["instance_port_bindings"] == []
    assert len(parent_connectivity["parent_signal_bindings"]) == 1
    child_pin = parent_connectivity["parent_signal_bindings"][0]
    assert child_pin["instance_path"] == "tb_top.dut"
    assert child_pin["pin"] == "clk"
    assert child_pin["parent_signal"] == "clk"
    assert child_pin["port_direction"] == "input"
    assert child_pin["relationship"] == "parent_signal_to_child_input"

    source_child_pin = next(
        item
        for item in parent["connectivity"]["loads"]
        if item["kind"] == "instance_port" and item["port"] == "clk"
    )
    assert source_child_pin["role"] == "load"
    assert source_child_pin["elaborated_pin_binding_resolution"] == "matched"
    assert source_child_pin["elaborated_pin_signal"] == "clk"
    assert source_child_pin["elaborated_pin_signal_consistent"] is True
    assert parent["connectivity"]["elaborated_pin_binding_evidence"]["status"] == (
        "NORMALIZED"
    )

    child = build_crossprobe(
        project,
        "tb_top.dut.count",
        waveform,
        design_index=design,
        elaborated_index=elaborated,
    )
    child_connectivity = child["elaborated_connectivity"]
    assert child_connectivity["parent_signal_bindings"] == []
    assert len(child_connectivity["instance_port_bindings"]) == 1
    parent_binding = child_connectivity["instance_port_bindings"][0]
    assert parent_binding["parent_instance_path"] == "tb_top"
    assert parent_binding["parent_signal"] == "count"
    assert parent_binding["port_direction"] == "output"
    assert parent_binding["relationship"] == "child_output_to_parent_signal"

    elaborated["port_evidence"] = {
        "status": "UNAVAILABLE",
        "source_format": "json",
        "reason": "direction_contract_not_normalized",
    }
    ungated = build_crossprobe(
        project,
        "TOP.tb_top.clk",
        waveform,
        design_index=design,
        elaborated_index=elaborated,
    )
    ungated_binding = ungated["elaborated_connectivity"]["parent_signal_bindings"][0]
    assert ungated_binding["port_direction"] is None
    assert ungated_binding["relationship"] == "direct_pin_varref"

def test_crossprobe_rejects_malformed_normalized_pin_binding_evidence(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="normalized pin_binding_evidence requires a pin_bindings list",
    ):
        build_crossprobe(
            project,
            "tb_top.dut.count",
            build_waveform_index(waveform_path, project_name=project.name),
            design_index=build_design_index(project),
            elaborated_index={
                "project": project.name,
                "top": project.top,
                "simulator": project.simulator,
                "instances": [
                    {
                        "path": "tb_top.dut",
                        "name": "dut",
                        "module": "counter",
                    }
                ],
                "pin_binding_evidence": {
                    "status": "NORMALIZED",
                    "source_format": "json",
                    "contract": "verilator_cell_pin_direct_varref_only",
                },
            },
        )


def test_crossprobe_ignores_stale_persisted_elaboration(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    elaborated_path = project.root / ".zddv" / "design" / "elaborated.json"
    elaborated_path.parent.mkdir(parents=True, exist_ok=True)
    elaborated_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": project.name,
                "top": "different_top",
                "simulator": project.simulator,
                "instances": [],
            }
        ),
        encoding="utf-8",
    )

    result = write_crossprobe_report(
        project,
        "tb_top.dut.count",
        input_path="trace.vcd",
    )

    assert result["status"] == "MATCHED"
    assert result["hierarchy_resolution"] == "source_structural"
    assert result["elaborated_evidence"]["status"] == "STALE"
    assert "top='different_top'" in result["elaborated_evidence"]["error"]
    assert "elaborated_index_path" not in result
    assert result["hierarchy"]["design_path"] == "tb_top.dut"


def test_crossprobe_rejects_persisted_elaboration_after_rtl_change(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    elaborated_path = project.root / ".zddv" / "design" / "elaborated.json"
    elaborated_path.parent.mkdir(parents=True, exist_ok=True)
    original_fingerprint = design_revision_fingerprint(project)
    elaborated_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": project.name,
                "top": project.top,
                "simulator": project.simulator,
                "design_fingerprint": original_fingerprint,
                "instances": [],
            }
        ),
        encoding="utf-8",
    )

    counter = project.root / "rtl" / "counter.sv"
    counter.write_text(
        counter.read_text(encoding="utf-8") + "\n// design revision changed\n",
        encoding="utf-8",
    )

    result = write_crossprobe_report(
        project,
        "tb_top.dut.count",
        input_path="trace.vcd",
    )

    assert result["status"] == "MATCHED"
    assert result["hierarchy_resolution"] == "source_structural"
    assert result["elaborated_evidence"]["status"] == "STALE"
    assert "design_fingerprint" in result["elaborated_evidence"]["error"]
    assert result["elaborated_evidence"]["design_fingerprint"] == original_fingerprint
    assert (
        result["elaborated_evidence"]["current_design_fingerprint"]
        == design_revision_fingerprint(project)
    )
    assert (
        result["elaborated_evidence"]["current_design_fingerprint"]
        != original_fingerprint
    )
    assert "elaborated_index_path" not in result


def test_crossprobe_rejects_mismatched_explicit_elaboration(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    with pytest.raises(ValueError, match="Elaborated index identity mismatch"):
        build_crossprobe(
            project,
            "tb_top.dut.count",
            build_waveform_index(waveform_path, project_name=project.name),
            design_index=build_design_index(project),
            elaborated_index={
                "project": project.name,
                "top": "wrong_top",
                "simulator": project.simulator,
                "instances": [],
            },
        )


def test_crossprobe_cli_supports_explicit_fst2vcd(tmp_path: Path, capsys, monkeypatch):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.fst"
    waveform_path.write_bytes(b"FST-placeholder")

    monkeypatch.setattr(
        "zddv.fst_adapter.shutil.which",
        lambda requested: "/usr/bin/fst2vcd" if requested == "fst2vcd" else None,
    )

    def fake_run(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text(VCD, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("zddv.fst_adapter.subprocess.run", fake_run)

    rc = main([
        "--project",
        str(project.root),
        "crossprobe",
        "tb_top.dut.count",
        "--input",
        "trace.fst",
        "--fst2vcd",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "CROSSPROBE MATCHED" in output
    report = json.loads(
        (project.root / ".zddv" / "debug" / "crossprobe.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["waveform"]["format"] == "fst"
    assert report["waveform"]["adapter"]["adapter"] == "fst2vcd"
    assert report["waveform"]["signal"]["path"] == "TOP.tb_top.dut.count"


def test_crossprobe_fst_stays_metadata_only_without_explicit_converter(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace-default.fst"
    waveform_path.write_bytes(b"FST-placeholder")

    with pytest.raises(RuntimeError, match="metadata-only"):
        write_crossprobe_report(
            project,
            "tb_top.dut.count",
            input_path="trace-default.fst",
        )

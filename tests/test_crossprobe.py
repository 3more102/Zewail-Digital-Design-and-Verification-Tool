from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.crossprobe import (
    build_source_waveform_crossprobe,
    write_source_waveform_crossprobe,
)


VCD = """$timescale 1ns $end
$scope module TOP $end
$scope module top $end
$scope module u_child $end
$var wire 1 ! y $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
"""


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "design.sv").write_text(
        """
module child(
    input logic a,
    output logic y
);
    assign y = a;
endmodule

module top;
    logic a;
    logic y;
    child u_child(.a(a), .y(y));
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)

    waveform = project.root / "trace.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    return project, waveform


def test_crossprobe_maps_source_hierarchy_to_waveform_suffix(tmp_path: Path):
    project, waveform = _project(tmp_path)

    report = build_source_waveform_crossprobe(
        project,
        unit="child",
        signal="y",
        input_path=waveform,
    )

    assert report["match_status"] == "hierarchy-matched"
    assert report["source"]["hierarchy_paths"] == ["top.u_child"]
    assert report["summary"]["drivers"] >= 1
    assert report["summary"]["loads"] >= 1
    assert report["summary"]["waveform_matches"] == 1
    assert report["waveform"]["matches"][0]["path"] == "TOP.top.u_child.y"
    assert report["waveform"]["matches"][0]["match"] == "hierarchy-suffix"


def test_crossprobe_reports_explicit_basename_fallback(tmp_path: Path):
    project, waveform = _project(tmp_path)
    waveform.write_text(
        """$timescale 1ns $end
$scope module simulator $end
$var wire 1 ! y $end
$upscope $end
$enddefinitions $end
#0
0!
""",
        encoding="utf-8",
    )

    report = build_source_waveform_crossprobe(
        project,
        unit="child",
        signal="y",
        input_path=waveform,
    )

    assert report["match_status"] == "basename-fallback-unique"
    assert report["summary"]["hierarchy_matches"] == 0
    assert report["summary"]["basename_fallback_matches"] == 1
    assert report["waveform"]["matches"][0]["match"] == "basename-fallback"


def test_write_crossprobe_report(tmp_path: Path):
    project, waveform = _project(tmp_path)

    result = write_source_waveform_crossprobe(
        project,
        unit="child",
        signal="y",
        input_path=waveform,
    )

    path = Path(result["path"])
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert '"match_status": "hierarchy-matched"' in text
    assert '"TOP.top.u_child.y"' in text


def test_crossprobe_cli(tmp_path: Path, capsys):
    project, waveform = _project(tmp_path)

    rc = main([
        "--project",
        str(project.root),
        "crossprobe",
        "y",
        "--unit",
        "child",
        "--input",
        str(waveform),
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "CROSSPROBE child.y" in output
    assert "top.u_child" in output
    assert "TOP.top.u_child.y" in output
    assert (project.root / ".zddv" / "debug" / "crossprobe.json").is_file()

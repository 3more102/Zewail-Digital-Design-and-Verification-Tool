import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.connectivity import (
    build_connectivity_index,
    find_signal,
    write_connectivity_index,
)


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "design.sv").write_text(
        """
module child(
    input  logic a,
    output logic y
);
    assign y = a;
endmodule

module top(
    input  logic clk,
    input  logic d,
    output logic q
);
    logic mid;

    child u_child(
        .a(d),
        .y(mid)
    );

    always_ff @(posedge clk) begin
        q <= mid;
    end
endmodule
""".lstrip(),
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.top = "top"
    save_project(project)
    return project


def _roles(item: dict, role: str) -> set[tuple[str, int, str]]:
    return {
        (ref["kind"], ref["line"], ref["detail"])
        for ref in item[f"{role}s"]
    }


def test_build_source_level_driver_load_index(tmp_path: Path):
    project = _project(tmp_path)

    index = build_connectivity_index(project)

    assert index["schema_version"] == 1
    assert index["analysis"] == "source-level"
    assert index["summary"]["units"] == 2

    child_a = find_signal(index, "a", unit="child")
    assert child_a["direction"] == "input"
    assert any(ref["kind"] == "port-boundary" for ref in child_a["drivers"])
    assert any(ref["kind"] == "expression" for ref in child_a["loads"])

    child_y = find_signal(index, "child.y")
    assert any(ref["kind"] == "assignment" for ref in child_y["drivers"])
    assert any(ref["kind"] == "port-boundary" for ref in child_y["loads"])

    top_d = find_signal(index, "d", unit="top")
    assert any(
        ref["kind"] == "instance-port" and "u_child.a" in ref["detail"]
        for ref in top_d["loads"]
    )

    top_mid = find_signal(index, "mid", unit="top")
    assert any(
        ref["kind"] == "instance-port" and "u_child.y" in ref["detail"]
        for ref in top_mid["drivers"]
    )
    assert any(ref["kind"] == "expression" for ref in top_mid["loads"])

    top_q = find_signal(index, "q", unit="top")
    assert any(ref["kind"] == "assignment" for ref in top_q["drivers"])
    assert any(ref["kind"] == "port-boundary" for ref in top_q["loads"])

    top_clk = find_signal(index, "clk", unit="top")
    assert any(ref["kind"] == "expression" for ref in top_clk["loads"])


def test_connectivity_index_is_written_as_json(tmp_path: Path):
    project = _project(tmp_path)

    result = write_connectivity_index(project)

    path = Path(result["path"])
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["analysis"] == "source-level"
    assert any(
        item["unit"] == "top" and item["signal"] == "mid"
        for item in payload["signals"]
    )


def test_find_signal_requires_disambiguation(tmp_path: Path):
    project = _project(tmp_path)
    extra = project.root / "rtl" / "extra.sv"
    extra.write_text(
        """
module other(input logic d);
endmodule
""".lstrip(),
        encoding="utf-8",
    )

    index = build_connectivity_index(project)

    try:
        find_signal(index, "d")
    except RuntimeError as exc:
        assert "ambiguous" in str(exc)
        assert "top.d" in str(exc)
        assert "other.d" in str(exc)
    else:
        raise AssertionError("Expected ambiguous signal lookup to fail")


def test_connectivity_and_signal_cli(tmp_path: Path, capsys):
    project = _project(tmp_path)

    rc = main(["--project", str(project.root), "connectivity"])
    assert rc == 0
    output = capsys.readouterr().out
    assert "CONNECTIVITY INDEX:" in output
    assert (project.root / ".zddv" / "design" / "connectivity.json").is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "signal",
            "mid",
            "--unit",
            "top",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "SIGNAL top.mid" in output
    assert "DRIVERS" in output
    assert "LOADS" in output
    assert "u_child.y" in output

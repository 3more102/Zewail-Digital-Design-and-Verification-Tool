from pathlib import Path

from zddv.config import ProjectConfig
from zddv.coverage_holes import (
    analyze_coverage_holes,
    coverage_point_location,
    generate_coverage_hole_report,
)


def test_compact_location_and_hole_ranking():
    points = [
        {
            "name": "fdesign.svl10n1pagev_line/top",
            "count": 0,
            "hit": False,
            "type": "line",
        },
        {
            "name": "fdesign.svl20n1pagev_branch/top",
            "count": 0,
            "hit": False,
            "type": "branch",
        },
        {
            "name": "fdesign.svl30n1pagev_toggle/top.sig",
            "count": 4,
            "hit": True,
            "type": "toggle",
        },
    ]

    result = analyze_coverage_holes(points, limit=10)

    assert result["total_holes"] == 2
    assert [row["type"] for row in result["holes"]] == [
        "branch",
        "line",
    ]
    assert result["holes"][0]["location"] == {
        "file": "design.sv",
        "line": 20,
        "hierarchy": "top",
    }


def test_control_character_metadata_location():
    name = (
        "\x01f\x01rtl/counter.sv"
        "\x01l\x0142"
        "\x01n\x011"
        "\x01page\x01v_line"
        "\x01hier\x01TOP.counter"
    )

    assert coverage_point_location(name) == {
        "file": "rtl/counter.sv",
        "line": 42,
        "hierarchy": "TOP.counter",
    }


def test_filter_coverage_holes_by_type():
    points = [
        {
            "name": "fdesign.svl10n1pagev_line/top",
            "count": 0,
            "hit": False,
            "type": "line",
        },
        {
            "name": "fdesign.svl20n1pagev_toggle/top.sig",
            "count": 0,
            "hit": False,
            "type": "toggle",
        },
    ]

    result = analyze_coverage_holes(
        points,
        kinds=("toggle",),
    )

    assert result["total_holes"] == 1
    assert result["holes"][0]["type"] == "toggle"


def test_generate_hole_report(tmp_path: Path):
    project = ProjectConfig(
        root=tmp_path,
        name="demo",
    )
    coverage_dir = tmp_path / ".zddv" / "coverage"
    coverage_dir.mkdir(parents=True)
    (coverage_dir / "coverage.dat").write_text(
        "# SystemC::Coverage-3\n"
        "C 'fdesign.svl10n1pagev_line/top' 0\n"
        "C 'fdesign.svl11n1pagev_line/top' 3\n",
        encoding="utf-8",
    )

    result = generate_coverage_hole_report(project)

    assert result["total_holes"] == 1
    assert Path(result["json_path"]).exists()
    assert Path(result["text_path"]).read_text(
        encoding="utf-8"
    ).startswith("Coverage holes: 1")

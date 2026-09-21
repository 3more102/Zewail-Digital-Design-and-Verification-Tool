import json
from pathlib import Path

from zddv.config import initialize_project
from zddv.functional_coverage import (
    ingest_functional_coverage,
    normalize_functional_coverage,
)
from zddv.storage import (
    list_functional_coverage_bins,
    list_functional_coverage_snapshots,
)


def test_normalize_functional_coverage_bins():
    result = normalize_functional_coverage(
        {
            "source": "unit-test",
            "bins": [
                {
                    "scope": "tb.axi",
                    "coverpoint": "burst_len",
                    "bin": "len1",
                    "hits": 4,
                    "goal": 1,
                },
                {
                    "scope": "tb.axi",
                    "coverpoint": "burst_len",
                    "bin": "len16",
                    "hits": 0,
                    "goal": 1,
                    "metadata": {"priority": "high"},
                },
            ],
        }
    )

    assert result["source"] == "unit-test"
    assert result["total_bins"] == 2
    assert result["covered_bins"] == 1
    assert result["uncovered_bins"] == 1
    assert result["coverage_rate"] == 50.0
    assert result["bins"][0]["status"] == "COVERED"
    assert result["bins"][1]["status"] == "UNCOVERED"


def test_invalid_functional_coverage_is_rejected():
    try:
        normalize_functional_coverage(
            {
                "bins": [
                    {
                        "coverpoint": "cp",
                        "bin": "bad",
                        "hits": -1,
                    }
                ]
            }
        )
    except ValueError as exc:
        assert "hits" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_ingest_functional_coverage_persists_snapshot_and_bins(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "fcov.json"
    source.write_text(
        json.dumps(
            {
                "source": "counter-example",
                "bins": [
                    {
                        "scope": "tb_counter",
                        "coverpoint": "final_count",
                        "bin": "eight",
                        "hits": 4,
                        "goal": 1,
                    },
                    {
                        "scope": "tb_counter",
                        "coverpoint": "corner",
                        "bin": "wrap",
                        "hits": 0,
                        "goal": 1,
                        "metadata": {"reason": "not targeted"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = ingest_functional_coverage(project, source)

    assert result["total_bins"] == 2
    assert result["covered_bins"] == 1
    assert Path(result["normalized_path"]).exists()

    snapshots = list_functional_coverage_snapshots(project, limit=10)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["coverage_rate"] == 50.0

    holes = list_functional_coverage_bins(
        project,
        result["snapshot_id"],
        status="UNCOVERED",
    )
    assert len(holes) == 1
    assert holes[0]["coverpoint"] == "corner"
    assert holes[0]["bin_name"] == "wrap"
    assert holes[0]["metadata"] == {"reason": "not targeted"}

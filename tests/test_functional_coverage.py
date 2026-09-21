from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.functional_coverage import (
    ingest_functional_coverage_log,
    parse_functional_coverage_log,
)
from zddv.storage import (
    functional_coverage_statistics,
    list_functional_coverage_bins,
)


def test_parse_normalized_functional_coverage_markers(tmp_path: Path):
    log = tmp_path / "simulation.log"
    log.write_text(
        "noise\n"
        "ZDDV_FCOV packet_cg opcode READ HITS=12 GOAL=1 observed\n"
        "ZDDV_FCOV packet_cg opcode RESERVED HITS=0\n",
        encoding="utf-8",
    )

    bins = parse_functional_coverage_log(log)

    assert bins == [
        {
            "bin_index": 0,
            "covergroup": "packet_cg",
            "coverpoint": "opcode",
            "bin_name": "READ",
            "hits": 12,
            "goal": 1,
            "message": "observed",
            "log_line": 2,
        },
        {
            "bin_index": 1,
            "covergroup": "packet_cg",
            "coverpoint": "opcode",
            "bin_name": "RESERVED",
            "hits": 0,
            "goal": 1,
            "message": None,
            "log_line": 3,
        },
    ]


def test_zero_goal_is_rejected(tmp_path: Path):
    log = tmp_path / "simulation.log"
    log.write_text(
        "ZDDV_FCOV packet_cg opcode READ HITS=1 GOAL=0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="goal must be >= 1"):
        parse_functional_coverage_log(log)


def test_functional_coverage_aggregates_across_runs(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    first = tmp_path / "run1.log"
    first.write_text(
        "ZDDV_FCOV packet_cg opcode READ HITS=0 GOAL=1\n"
        "ZDDV_FCOV packet_cg opcode RESERVED HITS=0 GOAL=1\n",
        encoding="utf-8",
    )
    second = tmp_path / "run2.log"
    second.write_text(
        "ZDDV_FCOV packet_cg opcode READ HITS=1 GOAL=1\n"
        "ZDDV_FCOV packet_cg opcode RESERVED HITS=0 GOAL=1\n",
        encoding="utf-8",
    )

    ingest_functional_coverage_log(
        project,
        run_id="run-1",
        log_path=first,
        created_at="2026-09-21T20:00:00+00:00",
    )
    ingest_functional_coverage_log(
        project,
        run_id="run-2",
        log_path=second,
        created_at="2026-09-21T20:01:00+00:00",
    )

    rows = list_functional_coverage_bins(project, limit=10)
    assert len(rows) == 2

    read = next(row for row in rows if row["bin_name"] == "READ")
    assert read["hits"] == 1
    assert read["goal"] == 1
    assert read["runs"] == 2
    assert read["covered"] is True

    reserved = next(row for row in rows if row["bin_name"] == "RESERVED")
    assert reserved["hits"] == 0
    assert reserved["covered"] is False

    uncovered = list_functional_coverage_bins(
        project,
        limit=10,
        uncovered_only=True,
    )
    assert [row["bin_name"] for row in uncovered] == ["RESERVED"]

    stats = functional_coverage_statistics(project)
    assert stats == {
        "total_bins": 2,
        "covered_bins": 1,
        "uncovered_bins": 1,
        "coverage_rate": 50.0,
    }


def test_functional_coverage_filters(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    log = tmp_path / "simulation.log"
    log.write_text(
        "ZDDV_FCOV packet_cg opcode READ HITS=1\n"
        "ZDDV_FCOV packet_cg length SHORT HITS=1\n"
        "ZDDV_FCOV response_cg status OK HITS=1\n",
        encoding="utf-8",
    )
    ingest_functional_coverage_log(
        project,
        run_id="run-filter",
        log_path=log,
        created_at="2026-09-21T20:00:00+00:00",
    )

    packet = list_functional_coverage_bins(
        project,
        limit=10,
        covergroup="packet_cg",
    )
    assert {row["coverpoint"] for row in packet} == {"opcode", "length"}

    opcode_stats = functional_coverage_statistics(
        project,
        covergroup="packet_cg",
        coverpoint="opcode",
    )
    assert opcode_stats["total_bins"] == 1
    assert opcode_stats["covered_bins"] == 1

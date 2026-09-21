from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from zddv.config import ProjectConfig


def merge_verilator_coverage(project: ProjectConfig) -> dict:
    tool = shutil.which("verilator_coverage")
    if tool is None:
        raise RuntimeError(
            "verilator_coverage was not found in PATH. Install Verilator and retry."
        )

    run_root = (project.root / project.run_dir).resolve()
    coverage_files = sorted(run_root.glob("*/coverage.dat"))
    if not coverage_files:
        raise RuntimeError(
            f"No coverage.dat files found under {run_root}. "
            "Run coverage-enabled simulations first."
        )

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    merged_path = out_dir / "coverage.dat"
    summary_path = out_dir / "summary.txt"

    merge_cmd = [
        tool,
        "--write",
        str(merged_path),
        *[str(path) for path in coverage_files],
    ]
    merge = subprocess.run(
        merge_cmd,
        cwd=project.root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if merge.returncode != 0:
        raise RuntimeError(
            "Coverage merge failed:\n" + (merge.stdout.strip() or "unknown error")
        )

    report_cmd = [tool, str(merged_path)]
    report = subprocess.run(
        report_cmd,
        cwd=project.root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    summary_path.write_text(report.stdout, encoding="utf-8")

    if report.returncode != 0:
        raise RuntimeError(
            f"Coverage report failed. See {summary_path}"
        )

    return {
        "inputs": [str(path) for path in coverage_files],
        "merged": str(merged_path),
        "summary": str(summary_path),
        "report": report.stdout,
    }

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from zddv.config import ProjectConfig


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


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
    inputs = [str(path) for path in coverage_files]

    # Verilator 5.020 uses the legacy -write/-read form. New releases accept
    # --write with positional inputs. Try legacy first for distro compatibility,
    # then fall back to the current syntax.
    merge_commands = [
        [tool, "-write", str(merged_path), "-read", *inputs],
        [tool, "--write", str(merged_path), *inputs],
    ]
    merge = None
    attempts: list[str] = []
    for command in merge_commands:
        merge = _run(command, project.root)
        attempts.append("$ " + " ".join(command) + "\n" + merge.stdout)
        if merge.returncode == 0 and merged_path.exists():
            break

    if merge is None or merge.returncode != 0 or not merged_path.exists():
        raise RuntimeError(
            "Coverage merge failed:\n" + "\n".join(attempts).strip()
        )

    report_cmd = [tool, str(merged_path)]
    report = _run(report_cmd, project.root)
    summary_path.write_text(report.stdout, encoding="utf-8")

    if report.returncode != 0:
        raise RuntimeError(f"Coverage report failed. See {summary_path}")

    return {
        "inputs": inputs,
        "merged": str(merged_path),
        "summary": str(summary_path),
        "report": report.stdout,
    }

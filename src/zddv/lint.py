from __future__ import annotations

from datetime import datetime, timezone
import json
import shutil
import subprocess

from zddv.config import ProjectConfig
from zddv.diagnostics import parse_verilator_diagnostics


def lint_project(project: ProjectConfig) -> dict:
    tool = shutil.which("verilator")
    if tool is None:
        raise RuntimeError(
            "Verilator was not found in PATH. Install Verilator and retry."
        )

    sources = project.source_files()
    if not sources:
        raise RuntimeError("No RTL/testbench sources matched the project configuration.")

    out_dir = (project.root / ".zddv" / "lint").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "lint.log"
    summary_path = out_dir / "lint.json"

    command = [
        tool,
        "--lint-only",
        "--timing",
        "--assert",
        "--Wno-fatal",
        "--top-module",
        project.top,
        *[str(path) for path in sources],
    ]
    completed = subprocess.run(
        command,
        cwd=project.root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log_path.write_text(completed.stdout, encoding="utf-8")

    diagnostics = parse_verilator_diagnostics(completed.stdout)
    errors = sum(item.severity == "ERROR" for item in diagnostics)
    warnings = sum(item.severity == "WARNING" for item in diagnostics)
    status = "PASS" if completed.returncode == 0 and errors == 0 else "FAIL"

    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": project.name,
        "simulator": "verilator",
        "top": project.top,
        "status": status,
        "returncode": completed.returncode,
        "errors": errors,
        "warnings": warnings,
        "sources": [str(path) for path in sources],
        "command": command,
        "log": str(log_path),
        "diagnostics": [item.as_dict() for item in diagnostics],
    }
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["summary"] = str(summary_path)
    return result

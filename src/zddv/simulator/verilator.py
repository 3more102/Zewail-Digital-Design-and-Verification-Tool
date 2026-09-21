from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from zddv.config import ProjectConfig
from .base import BuildResult, RunResult, SimulatorBackend


class VerilatorBackend(SimulatorBackend):
    name = "verilator"

    def _tool(self) -> str:
        tool = shutil.which("verilator")
        if tool is None:
            raise RuntimeError(
                "Verilator was not found in PATH. Install Verilator and retry."
            )
        return tool

    def version(self) -> str:
        result = subprocess.run(
            [self._tool(), "--version"],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Unable to query Verilator.")
        return result.stdout.strip()

    def _build_dir(self, project: ProjectConfig) -> Path:
        return (project.root / project.build_dir).resolve()

    def _executable(self, project: ProjectConfig) -> Path:
        name = "zddv_sim.exe" if os.name == "nt" else "zddv_sim"
        return self._build_dir(project) / name

    @staticmethod
    def _cpp_top_name(top: str) -> str:
        return "V" + re.sub(r"[^A-Za-z0-9_]", "_", top)

    def _write_main(self, project: ProjectConfig, build_dir: Path) -> Path:
        cpp_top = self._cpp_top_name(project.top)
        path = build_dir / "zddv_main.cpp"
        path.write_text(
            f"""#include <memory>
#include "verilated.h"
#include "verilated_cov.h"
#include "{cpp_top}.h"

int main(int argc, char** argv) {{
    const std::unique_ptr<VerilatedContext> contextp{{new VerilatedContext}};

#if VM_TRACE
    contextp->traceEverOn(true);
#endif

    contextp->commandArgs(argc, argv);
    const std::unique_ptr<{cpp_top}> topp{{new {cpp_top}{{contextp.get()}}}};

    while (!contextp->gotFinish()) {{
        topp->eval();
        if (!topp->eventsPending()) break;
        contextp->time(topp->nextTimeSlot());
    }}

    topp->final();

#if VM_COVERAGE
    contextp->coveragep()->write("coverage.dat");
#endif

    return 0;
}}
""",
            encoding="utf-8",
        )
        return path

    def build(self, project: ProjectConfig) -> BuildResult:
        sources = project.source_files()
        if not sources:
            raise RuntimeError("No RTL/testbench sources matched the project configuration.")

        build_dir = self._build_dir(project)
        build_dir.mkdir(parents=True, exist_ok=True)
        log_path = build_dir / "build.log"

        if project.coverage:
            # Verilator versions before 5.050 do not automatically dump
            # coverage from --main/--binary. Use an explicit wrapper so ZDDV
            # remains compatible with older packaged releases such as 5.020.
            main_cpp = self._write_main(project, build_dir)
            command = [
                self._tool(),
                "--cc",
                "--exe",
                "--build",
                "--timing",
                "--Wno-fatal",
            ]
        else:
            main_cpp = None
            command = [
                self._tool(),
                "--binary",
                "--timing",
                "--Wno-fatal",
            ]

        if project.waveform:
            command.append("--trace")
        if project.coverage:
            command.append("--coverage")

        command.extend(
            [
                "--top-module",
                project.top,
                "-Mdir",
                str(build_dir),
                "-o",
                "zddv_sim",
                *[str(path) for path in sources],
            ]
        )
        if main_cpp is not None:
            command.append(str(main_cpp))

        completed = subprocess.run(
            command,
            cwd=project.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        log_path.write_text(completed.stdout, encoding="utf-8")

        executable = self._executable(project)
        if completed.returncode != 0 or not executable.exists():
            executable = None

        manifest = {
            "simulator": self.name,
            "simulator_version": self.version(),
            "top": project.top,
            "sources": [str(p) for p in sources],
            "waveform": project.waveform,
            "coverage": project.coverage,
            "custom_main": str(main_cpp) if main_cpp else None,
            "command": command,
            "returncode": completed.returncode,
            "executable": str(executable) if executable else None,
        }
        (build_dir / "build.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

        return BuildResult(
            command=command,
            returncode=completed.returncode,
            log_path=log_path,
            executable=executable,
        )

    @staticmethod
    def _safe_label(value: str) -> str:
        label = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
        return label[:40] or "test"

    def run(
        self,
        project: ProjectConfig,
        *,
        test_name: str | None = None,
        seed: int | None = None,
        plusargs: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> RunResult:
        executable = self._executable(project)
        if not executable.exists():
            build = self.build(project)
            if not build.passed or build.executable is None:
                raise RuntimeError(f"Build failed. See {build.log_path}")
            executable = build.executable

        now = datetime.now(timezone.utc)
        parts = [now.strftime("%Y%m%dT%H%M%SZ")]
        if test_name:
            parts.append(self._safe_label(test_name))
        if seed is not None:
            parts.append(f"s{seed}")
        parts.append(uuid.uuid4().hex[:8])
        run_id = "-".join(parts)

        run_dir = (project.root / project.run_dir / run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=False)

        command = [str(executable)]
        if test_name:
            command.append(f"+ZDDV_TEST={test_name}")
        if seed is not None:
            command.extend([f"+ZDDV_SEED={seed}", f"+verilator+seed+{seed}"])
        command.extend(plusargs or [])

        timed_out = False
        try:
            completed = subprocess.run(
                command,
                cwd=run_dir,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
            returncode = completed.returncode
            output = completed.stdout
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = 124
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            output += f"\nZDDV_TIMEOUT after {timeout_s} seconds\n"

        log_path = run_dir / "simulation.log"
        log_path.write_text(output, encoding="utf-8")

        waveform = None
        for name in ("waveform.vcd", "dump.vcd", "waveform.fst", "dump.fst"):
            candidate = run_dir / name
            if candidate.exists():
                waveform = candidate
                break

        coverage = run_dir / "coverage.dat"
        if not coverage.exists():
            coverage = None

        status = "TIMEOUT" if timed_out else ("PASS" if returncode == 0 else "FAIL")
        record = {
            "run_id": run_id,
            "created_at": now.isoformat(),
            "project": project.name,
            "test": test_name,
            "seed": seed,
            "plusargs": plusargs or [],
            "timeout_s": timeout_s,
            "simulator": self.name,
            "simulator_version": self.version(),
            "top": project.top,
            "command": command,
            "returncode": returncode,
            "status": status,
            "log": str(log_path),
            "waveform": str(waveform) if waveform else None,
            "coverage": str(coverage) if coverage else None,
        }
        (run_dir / "run.json").write_text(
            json.dumps(record, indent=2),
            encoding="utf-8",
        )

        return RunResult(
            run_id=run_id,
            command=command,
            returncode=returncode,
            status=status,
            run_dir=run_dir,
            log_path=log_path,
            waveform_path=waveform,
            coverage_path=coverage,
            test_name=test_name,
            seed=seed,
        )

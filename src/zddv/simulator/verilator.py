from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from zddv.assertions import parse_verilator_assertions
from zddv.config import ProjectConfig
from zddv.storage import record_assertion_events, record_run
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

    @staticmethod
    def _version_number(version_text: str) -> tuple[int, int] | None:
        match = re.search(r"\\bVerilator\\s+(\\d+)\\.(\\d+)", version_text)
        if match is None:
            return None
        return int(match.group(1)), int(match.group(2))

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
        simulator_version = self.version()

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

        version_number = self._version_number(simulator_version)
        if version_number is not None and version_number < (5, 38):
            command.append("--assert")

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
            "simulator_version": simulator_version,
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
        started = time.perf_counter()
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

        duration_ms = (time.perf_counter() - started) * 1000.0

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
        assertion_events = parse_verilator_assertions(output)
        assertions_path = run_dir / "assertions.json"
        assertions_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "count": len(assertion_events),
                    "events": assertion_events,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

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
            "duration_ms": round(duration_ms, 3),
            "run_dir": str(run_dir),
            "log": str(log_path),
            "waveform": str(waveform) if waveform else None,
            "coverage": str(coverage) if coverage else None,
            "assertion_count": len(assertion_events),
            "assertions": str(assertions_path),
        }
        (run_dir / "run.json").write_text(
            json.dumps(record, indent=2),
            encoding="utf-8",
        )
        record_run(project, record)
        record_assertion_events(project, record, assertion_events)

        return RunResult(
            run_id=run_id,
            command=command,
            returncode=returncode,
            status=status,
            run_dir=run_dir,
            log_path=log_path,
            waveform_path=waveform,
            coverage_path=coverage,
            assertion_count=len(assertion_events),
            assertions_path=assertions_path,
            test_name=test_name,
            seed=seed,
        )

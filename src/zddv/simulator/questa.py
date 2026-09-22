from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from zddv.assertions import ingest_assertion_log
from zddv.config import ProjectConfig
from zddv.storage import record_run
from zddv.uvm import analyze_uvm_log
from .base import BuildResult, RunResult, SimulatorBackend


class QuestaBackend(SimulatorBackend):
    """Questa compile/run adapter using the native vlib/vlog/vsim flow."""

    name = "questa"

    @staticmethod
    def _tool(name: str) -> str:
        tool = shutil.which(name)
        if tool is None:
            raise RuntimeError(
                f"Questa {name} was not found in PATH. "
                "Install/configure Questa and retry."
            )
        return tool

    def version(self) -> str:
        result = subprocess.run(
            [self._tool("vsim"), "-version"],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or "Unable to query Questa version.")
        return result.stdout.strip() or result.stderr.strip()

    def _build_dir(self, project: ProjectConfig) -> Path:
        return (project.root / project.build_dir).resolve()

    def _work_library(self, project: ProjectConfig) -> Path:
        return self._build_dir(project) / "work"

    @staticmethod
    def _safe_label(value: str) -> str:
        label = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
        return label[:40] or "test"

    def build(self, project: ProjectConfig) -> BuildResult:
        sources = project.source_files()
        if not sources:
            raise RuntimeError(
                "No RTL/testbench sources matched the project configuration."
            )

        simulator_version = self.version()
        vlib = self._tool("vlib")
        vlog = self._tool("vlog")

        build_dir = self._build_dir(project)
        build_dir.mkdir(parents=True, exist_ok=True)
        work_library = self._work_library(project)
        if work_library.exists():
            shutil.rmtree(work_library)

        log_path = build_dir / "build.log"
        library_command = [vlib, "work"]
        compile_command = [
            vlog,
            "-sv",
            "-work",
            "work",
        ]
        if project.coverage:
            compile_command.append("+cover=bcesft")
        compile_command.extend(str(path) for path in sources)

        library_result = subprocess.run(
            library_command,
            cwd=build_dir,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        compile_result = None
        if library_result.returncode == 0:
            compile_result = subprocess.run(
                compile_command,
                cwd=build_dir,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

        returncode = (
            compile_result.returncode
            if compile_result is not None
            else library_result.returncode
        )
        output_parts = [
            "$ " + " ".join(library_command),
            library_result.stdout or "",
        ]
        if compile_result is not None:
            output_parts.extend([
                "$ " + " ".join(compile_command),
                compile_result.stdout or "",
            ])
        log_path.write_text("\n".join(output_parts), encoding="utf-8")

        artifact = (
            work_library
            if returncode == 0 and work_library.exists()
            else None
        )
        manifest = {
            "simulator": self.name,
            "simulator_version": simulator_version,
            "top": project.top,
            "sources": [str(path) for path in sources],
            "waveform": project.waveform,
            "coverage_requested": project.coverage,
            "coverage_capture": "questa_ucdb" if project.coverage else "disabled",
            "library_command": library_command,
            "compile_command": compile_command,
            "returncode": returncode,
            "build_artifact": str(artifact) if artifact else None,
        }
        (build_dir / "build.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

        return BuildResult(
            command=compile_command,
            returncode=returncode,
            log_path=log_path,
            executable=None,
            artifact=artifact,
        )

    def _write_do_file(
        self,
        run_dir: Path,
        *,
        waveform: bool,
        coverage: bool,
    ) -> Path:
        path = run_dir / "zddv_questa.do"
        lines = [
            "onerror {quit -code 2 -f}",
            "onbreak {quit -code 2 -f}",
        ]
        if waveform:
            lines.extend([
                "vcd file waveform.vcd",
                "vcd add -r /*",
            ])
        if coverage:
            lines.append("coverage save -onexit coverage.ucdb")
        lines.append("run -all")
        if waveform:
            lines.append("vcd flush")
        lines.append("quit -code 0 -f")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def run(
        self,
        project: ProjectConfig,
        *,
        test_name: str | None = None,
        seed: int | None = None,
        plusargs: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> RunResult:
        work_library = self._work_library(project)
        if not work_library.exists():
            build = self.build(project)
            if not build.passed or build.artifact is None:
                raise RuntimeError(f"Build failed. See {build.log_path}")
            work_library = build.artifact

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
        do_file = self._write_do_file(
            run_dir,
            waveform=project.waveform,
            coverage=project.coverage,
        )

        command = [
            self._tool("vsim"),
            "-c",
            "-lib",
            str(work_library),
        ]
        if seed is not None:
            command.extend(["-sv_seed", str(seed)])
        if project.coverage:
            command.append("-coverage")
        if project.waveform:
            command.append("-voptargs=+acc")
        command.append(project.top)
        runtime_plusargs = list(plusargs or [])
        if test_name:
            command.append(f"+ZDDV_TEST={test_name}")
            if not any(
                arg.startswith("+UVM_TESTNAME=")
                for arg in runtime_plusargs
            ):
                command.append(f"+UVM_TESTNAME={test_name}")
        if seed is not None:
            command.append(f"+ZDDV_SEED={seed}")
        command.extend(runtime_plusargs)
        command.extend(["-do", str(do_file)])

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
        log_path.write_text(output or "", encoding="utf-8")

        waveform_path = run_dir / "waveform.vcd"
        if not waveform_path.exists():
            waveform_path = None

        coverage_path = run_dir / "coverage.ucdb"
        if not coverage_path.exists():
            coverage_path = None

        status = "TIMEOUT" if timed_out else ("PASS" if returncode == 0 else "FAIL")
        simulator_version = self.version()
        record = {
            "run_id": run_id,
            "created_at": now.isoformat(),
            "project": project.name,
            "test": test_name,
            "seed": seed,
            "plusargs": runtime_plusargs,
            "timeout_s": timeout_s,
            "simulator": self.name,
            "simulator_version": simulator_version,
            "top": project.top,
            "command": command,
            "returncode": returncode,
            "status": status,
            "duration_ms": round(duration_ms, 3),
            "run_dir": str(run_dir),
            "log": str(log_path),
            "waveform": str(waveform_path) if waveform_path else None,
            "coverage": str(coverage_path) if coverage_path else None,
            "coverage_capture": "questa_ucdb" if project.coverage else "disabled",
            "build_artifact": str(work_library),
        }
        (run_dir / "run.json").write_text(
            json.dumps(record, indent=2),
            encoding="utf-8",
        )
        record_run(project, record)
        ingest_assertion_log(
            project,
            run_id=run_id,
            log_path=log_path,
            created_at=now.isoformat(),
        )
        if "UVM_" in (output or ""):
            analyze_uvm_log(
                project,
                None,
                source="questa-run",
                run_id=run_id,
            )

        return RunResult(
            run_id=run_id,
            command=command,
            returncode=returncode,
            status=status,
            run_dir=run_dir,
            log_path=log_path,
            waveform_path=waveform_path,
            coverage_path=coverage_path,
            test_name=test_name,
            seed=seed,
        )

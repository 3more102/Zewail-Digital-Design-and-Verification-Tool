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


class VcsBackend(SimulatorBackend):
    """Synopsys VCS compile/run adapter using the native vcs -> simv flow."""

    name = "vcs"
    coverage_metrics = "line+cond+fsm+tgl+branch"

    @staticmethod
    def _tool() -> str:
        tool = shutil.which("vcs")
        if tool is None:
            raise RuntimeError(
                "Synopsys VCS was not found in PATH. "
                "Install/configure VCS and retry."
            )
        return tool

    def version(self) -> str:
        tool = self._tool()
        diagnostics: list[str] = []
        for flag in ("-id", "-ID"):
            result = subprocess.run(
                [tool, flag],
                check=False,
                text=True,
                capture_output=True,
            )
            detail = result.stdout.strip() or result.stderr.strip()
            if result.returncode == 0 and detail:
                return detail
            if detail:
                diagnostics.append(detail)

        raise RuntimeError(
            diagnostics[-1]
            if diagnostics
            else "Unable to query Synopsys VCS version."
        )

    def _build_dir(self, project: ProjectConfig) -> Path:
        return (project.root / project.build_dir).resolve()

    def _executable(self, project: ProjectConfig) -> Path:
        return self._build_dir(project) / "simv"

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
        vcs = self._tool()
        build_dir = self._build_dir(project)
        build_dir.mkdir(parents=True, exist_ok=True)
        executable = self._executable(project)
        log_path = build_dir / "build.log"

        command = [
            vcs,
            "-full64",
            "-sverilog",
            "-ntb_opts",
            "uvm-1.2",
            "-top",
            project.top,
            "-o",
            str(executable),
        ]
        if project.coverage:
            command.extend(["-cm", self.coverage_metrics])
        command.extend(str(path) for path in sources)

        completed = subprocess.run(
            command,
            cwd=build_dir,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        log_path.write_text(completed.stdout or "", encoding="utf-8")

        built_executable = (
            executable
            if completed.returncode == 0 and executable.exists()
            else None
        )
        coverage_capture = "instrumented" if project.coverage else "disabled"
        manifest = {
            "simulator": self.name,
            "simulator_version": simulator_version,
            "top": project.top,
            "sources": [str(path) for path in sources],
            "waveform_requested": project.waveform,
            "waveform_capture": "vcd" if project.waveform else "disabled",
            "coverage_requested": project.coverage,
            "coverage_capture": coverage_capture,
            "coverage_metrics": self.coverage_metrics if project.coverage else None,
            "uvm_library": "uvm-1.2",
            "command": command,
            "returncode": completed.returncode,
            "executable": str(built_executable) if built_executable else None,
        }
        (build_dir / "build.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

        return BuildResult(
            command=command,
            returncode=completed.returncode,
            log_path=log_path,
            executable=built_executable,
        )

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

        runtime_plusargs = list(plusargs or [])
        command = [str(executable)]
        coverage_path = run_dir / "coverage.vdb" if project.coverage else None
        if coverage_path is not None:
            command.extend(["-cm", self.coverage_metrics, "-cm_dir", str(coverage_path)])
        if project.waveform:
            command.append("+vcs+dumpvars+waveform.vcd")
        if test_name:
            command.append(f"+ZDDV_TEST={test_name}")
            if not any(
                arg.startswith("+UVM_TESTNAME=")
                for arg in runtime_plusargs
            ):
                command.append(f"+UVM_TESTNAME={test_name}")
        if seed is not None:
            command.extend([
                f"+ZDDV_SEED={seed}",
                f"+ntb_random_seed={seed}",
            ])
        command.extend(runtime_plusargs)

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
            output = completed.stdout or ""
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

        waveform_path = run_dir / "waveform.vcd"
        if not waveform_path.exists():
            waveform_path = None

        if coverage_path is not None and not coverage_path.exists():
            coverage_path = None
        coverage_capture = (
            "vdb"
            if coverage_path is not None
            else ("missing" if project.coverage else "disabled")
        )
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
            "coverage_requested": project.coverage,
            "coverage": str(coverage_path) if coverage_path else None,
            "coverage_capture": coverage_capture,
            "coverage_metrics": self.coverage_metrics if project.coverage else None,
            "build_artifact": str(executable),
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
        if "UVM_" in output:
            analyze_uvm_log(
                project,
                None,
                source="vcs-run",
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

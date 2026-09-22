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
from zddv.uvm_marker import analyze_uvm_marker_log, has_explicit_uvm_markers
from .base import BuildResult, RunResult, SimulatorBackend


class XceliumBackend(SimulatorBackend):
    """Cadence Xcelium adapter using the native xrun elaborate/run flow."""

    name = "xcelium"

    @staticmethod
    def _tool() -> str:
        tool = shutil.which("xrun")
        if tool is None:
            raise RuntimeError(
                "Cadence Xcelium xrun was not found in PATH. "
                "Install/configure Xcelium and retry."
            )
        return tool

    def version(self) -> str:
        result = subprocess.run(
            [self._tool(), "-version"],
            check=False,
            text=True,
            capture_output=True,
        )
        detail = result.stdout.strip() or result.stderr.strip()
        if result.returncode != 0:
            raise RuntimeError(detail or "Unable to query Cadence Xcelium version.")
        if not detail:
            raise RuntimeError("Cadence Xcelium returned an empty version response.")
        return detail

    def _build_dir(self, project: ProjectConfig) -> Path:
        return (project.root / project.build_dir).resolve()

    def _library_dir(self, project: ProjectConfig) -> Path:
        return self._build_dir(project) / "xcelium.d"

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
        xrun = self._tool()
        build_dir = self._build_dir(project)
        build_dir.mkdir(parents=True, exist_ok=True)
        library_dir = self._library_dir(project)
        if library_dir.exists():
            shutil.rmtree(library_dir)

        log_path = build_dir / "build.log"
        command = [
            xrun,
            "-sv",
            "-uvm",
            "-top",
            project.top,
            "-xmlibdirname",
            str(library_dir),
            "-elaborate",
        ]
        if project.waveform:
            command.extend(["-access", "+rwc"])
        if project.coverage:
            command.extend(["-coverage", "all"])
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

        artifact = (
            library_dir
            if completed.returncode == 0 and library_dir.exists()
            else None
        )
        manifest = {
            "simulator": self.name,
            "simulator_version": simulator_version,
            "top": project.top,
            "sources": [str(path) for path in sources],
            "waveform_requested": project.waveform,
            "waveform_capture": "vcd-tcl" if project.waveform else "disabled",
            "coverage_requested": project.coverage,
            "coverage_capture": "instrumented" if project.coverage else "disabled",
            "coverage_metrics": "all" if project.coverage else None,
            "uvm": "xrun-native",
            "command": command,
            "returncode": completed.returncode,
            "build_artifact": str(artifact) if artifact else None,
        }
        (build_dir / "build.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )

        return BuildResult(
            command=command,
            returncode=completed.returncode,
            log_path=log_path,
            executable=None,
            artifact=artifact,
        )

    @staticmethod
    def _write_input_file(run_dir: Path, *, waveform: bool) -> Path | None:
        if not waveform:
            return None
        path = run_dir / "zddv_xcelium.tcl"
        path.write_text(
            "\n".join(
                [
                    "database -open zddv_vcd -vcd -into waveform.vcd",
                    (
                        "probe -create -database zddv_vcd "
                        "[scope -tops] -depth all -all"
                    ),
                    "run",
                    "exit",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
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
        library_dir = self._library_dir(project)
        if not library_dir.exists():
            build = self.build(project)
            if not build.passed or build.artifact is None:
                raise RuntimeError(f"Build failed. See {build.log_path}")
            library_dir = build.artifact

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
        input_file = self._write_input_file(
            run_dir,
            waveform=project.waveform,
        )

        command = [
            self._tool(),
            "-R",
            "-xmlibdirname",
            str(library_dir),
        ]
        if seed is not None:
            command.extend(["-svseed", str(seed)])
        if input_file is not None:
            command.extend(["-input", str(input_file)])

        expected_coverage_path = (
            run_dir / "coverage" / run_id
            if project.coverage
            else None
        )
        if project.coverage:
            command.extend(
                [
                    "-covworkdir",
                    str(run_dir),
                    "-covscope",
                    "coverage",
                    "-covtest",
                    run_id,
                    "-covmodeldir",
                    str(run_dir / "coverage"),
                    "-covoverwrite",
                ]
            )

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

        coverage_path = expected_coverage_path
        if coverage_path is not None:
            has_ucd = coverage_path.is_dir() and any(
                coverage_path.glob("*.ucd")
            )
            if not has_ucd:
                coverage_path = None
        coverage_capture = (
            "ucd-run-db"
            if coverage_path is not None
            else ("missing" if project.coverage else "disabled")
        )

        status = (
            "TIMEOUT"
            if timed_out
            else ("PASS" if returncode == 0 else "FAIL")
        )
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
            "coverage_metrics": "all" if project.coverage else None,
            "build_artifact": str(library_dir),
        }
        (run_dir / "run.json").write_text(
            json.dumps(record, indent=2) + "\n",
            encoding="utf-8",
        )
        record_run(project, record)
        ingest_assertion_log(
            project,
            run_id=run_id,
            log_path=log_path,
            created_at=now.isoformat(),
        )
        if any(
            token in (output or "")
            for token in ("UVM_INFO", "UVM_WARNING", "UVM_ERROR", "UVM_FATAL")
        ):
            analyze_uvm_log(
                project,
                None,
                source="xcelium-run",
                run_id=run_id,
            )
        if has_explicit_uvm_markers(output or ""):
            analyze_uvm_marker_log(
                project,
                None,
                source="xcelium-marker-run",
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

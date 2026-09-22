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
from .base import BuildResult, RunResult, SimulatorBackend


class QuestaBackend(SimulatorBackend):
    """Siemens Questa/QuestaSim batch-mode simulator adapter.

    The adapter intentionally uses the long-established vlib/vlog/vsim flow
    instead of requiring qrun so it can cover a wider range of Questa-family
    installations, including environments that expose the classic commands.
    """

    name = "questa"

    def _tool(self, name: str) -> str:
        tool = shutil.which(name)
        if tool is None:
            raise RuntimeError(
                f"Siemens Questa executable '{name}' was not found in PATH."
            )
        return tool

    def version(self) -> str:
        result = subprocess.run(
            [self._tool("vsim"), "-version"],
            check=False,
            text=True,
            capture_output=True,
        )
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0:
            raise RuntimeError(output or "Unable to query Siemens Questa version.")
        return output.splitlines()[0] if output else "Questa"

    def _build_dir(self, project: ProjectConfig) -> Path:
        return (project.root / project.build_dir / "questa").resolve()

    def _library_dir(self, project: ProjectConfig) -> Path:
        return self._build_dir(project) / "work"

    def _manifest_path(self, project: ProjectConfig) -> Path:
        return self._build_dir(project) / "build.json"

    @staticmethod
    def _safe_label(value: str) -> str:
        label = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
        return label[:40] or "test"

    @staticmethod
    def _run_tool(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def build(self, project: ProjectConfig) -> BuildResult:
        sources = project.source_files()
        if not sources:
            raise RuntimeError("No RTL/testbench sources matched the project configuration.")

        build_dir = self._build_dir(project)
        build_dir.mkdir(parents=True, exist_ok=True)
        log_path = build_dir / "build.log"
        library_dir = self._library_dir(project)

        vlib_command = [self._tool("vlib"), "work"]
        vlib_result = self._run_tool(vlib_command, cwd=build_dir)

        vlog_command = [
            self._tool("vlog"),
            "-sv",
            "-work",
            "work",
            *[str(path) for path in sources],
        ]
        if vlib_result.returncode == 0:
            vlog_result = self._run_tool(vlog_command, cwd=build_dir)
        else:
            vlog_result = subprocess.CompletedProcess(
                vlog_command,
                vlib_result.returncode,
                stdout="Skipped because vlib failed.\n",
            )

        log_parts = [
            "$ " + " ".join(vlib_command),
            vlib_result.stdout or "",
            "$ " + " ".join(vlog_command),
            vlog_result.stdout or "",
        ]
        log_path.write_text("\n".join(log_parts), encoding="utf-8")

        returncode = (
            vlib_result.returncode
            if vlib_result.returncode != 0
            else vlog_result.returncode
        )
        manifest_path = self._manifest_path(project)
        manifest = {
            "simulator": self.name,
            "simulator_version": self.version(),
            "top": project.top,
            "sources": [str(path) for path in sources],
            "library": str(library_dir),
            "commands": [vlib_command, vlog_command],
            "returncode": returncode,
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )

        return BuildResult(
            command=vlog_command,
            returncode=returncode,
            log_path=log_path,
            # Questa elaborates/optimizes at simulation launch.  The build
            # manifest is the durable artifact proving compilation succeeded.
            executable=manifest_path if returncode == 0 else None,
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
        manifest_path = self._manifest_path(project)
        if not manifest_path.exists():
            build = self.build(project)
            if not build.passed:
                raise RuntimeError(f"Build failed. See {build.log_path}")

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

        waveform_target = run_dir / "waveform.wlf"
        command = [
            self._tool("vsim"),
            "-c",
            "-lib",
            str(self._library_dir(project)),
        ]
        if seed is not None:
            command.extend(["-sv_seed", str(seed)])
        if project.waveform:
            command.extend(["-wlf", str(waveform_target)])
        command.append(project.top)

        runtime_plusargs = list(plusargs or [])
        if test_name:
            command.append(f"+ZDDV_TEST={test_name}")
            if not any(arg.startswith("+UVM_TESTNAME=") for arg in runtime_plusargs):
                command.append(f"+UVM_TESTNAME={test_name}")
        command.extend(runtime_plusargs)
        command.extend(["-do", "run -all; quit -f"])

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

        waveform = waveform_target if waveform_target.exists() else None
        status = "TIMEOUT" if timed_out else ("PASS" if returncode == 0 else "FAIL")
        record = {
            "run_id": run_id,
            "created_at": now.isoformat(),
            "project": project.name,
            "test": test_name,
            "seed": seed,
            "plusargs": runtime_plusargs,
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
            "coverage": None,
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

        return RunResult(
            run_id=run_id,
            command=command,
            returncode=returncode,
            status=status,
            run_dir=run_dir,
            log_path=log_path,
            waveform_path=waveform,
            coverage_path=None,
            test_name=test_name,
            seed=seed,
        )

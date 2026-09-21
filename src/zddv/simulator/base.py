from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from zddv.config import ProjectConfig


@dataclass(frozen=True)
class BuildResult:
    command: list[str]
    returncode: int
    log_path: Path
    executable: Path | None

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and self.executable is not None


@dataclass(frozen=True)
class RunResult:
    run_id: str
    command: list[str]
    returncode: int
    status: str
    run_dir: Path
    log_path: Path
    waveform_path: Path | None
    coverage_path: Path | None = None
    test_name: str | None = None
    seed: int | None = None


class SimulatorBackend(ABC):
    name: str

    @abstractmethod
    def version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def build(self, project: ProjectConfig) -> BuildResult:
        raise NotImplementedError

    @abstractmethod
    def run(
        self,
        project: ProjectConfig,
        *,
        test_name: str | None = None,
        seed: int | None = None,
        plusargs: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> RunResult:
        raise NotImplementedError

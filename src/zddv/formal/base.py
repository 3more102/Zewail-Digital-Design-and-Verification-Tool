from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig


@dataclass(frozen=True)
class FormalRunRequest:
    property_names: tuple[str, ...] = ()
    depth: int | None = None
    timeout_s: float | None = None

    def __post_init__(self) -> None:
        if self.depth is not None:
            if isinstance(self.depth, bool) or not isinstance(self.depth, int):
                raise TypeError("depth must be an integer or None")
            if self.depth < 0:
                raise ValueError("depth must be >= 0")

        if self.timeout_s is not None:
            if isinstance(self.timeout_s, bool) or not isinstance(
                self.timeout_s, (int, float)
            ):
                raise TypeError("timeout_s must be a number or None")
            if self.timeout_s <= 0:
                raise ValueError("timeout_s must be > 0")

        for index, name in enumerate(self.property_names):
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"property_names[{index}] must be a non-empty string"
                )


@dataclass(frozen=True)
class FormalRunResult:
    engine: str
    engine_version: str | None
    command: tuple[str, ...]
    returncode: int
    run_dir: Path
    log_path: Path
    result_path: Path | None = None

    @property
    def completed(self) -> bool:
        return self.returncode == 0


class FormalBackend(ABC):
    """Simulator-independent execution contract for future formal engines."""

    name: str

    @abstractmethod
    def version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def run(
        self,
        project: ProjectConfig,
        request: FormalRunRequest,
    ) -> FormalRunResult:
        raise NotImplementedError

    @abstractmethod
    def normalize(
        self,
        project: ProjectConfig,
        result: FormalRunResult,
    ) -> dict[str, Any]:
        """Return a normalized payload accepted by normalize_formal_data()."""
        raise NotImplementedError

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import glob
import tomllib


CONFIG_NAME = "zddv.toml"


@dataclass
class ProjectConfig:
    root: Path
    name: str
    top: str = "tb_top"
    simulator: str = "verilator"
    rtl: list[str] = field(default_factory=list)
    tb: list[str] = field(default_factory=list)
    build_dir: str = ".zddv/build"
    run_dir: str = ".zddv/runs"
    waveform: bool = True

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_NAME

    def source_files(self) -> list[Path]:
        files: list[Path] = []
        for pattern in [*self.rtl, *self.tb]:
            matches = [
                Path(p).resolve()
                for p in glob.glob(str(self.root / pattern), recursive=True)
                if Path(p).is_file()
            ]
            files.extend(matches)

        seen: set[Path] = set()
        ordered: list[Path] = []
        for path in files:
            if path not in seen:
                ordered.append(path)
                seen.add(path)
        return ordered


def load_project(path: str | Path = ".") -> ProjectConfig:
    candidate = Path(path).resolve()
    config_path = candidate if candidate.is_file() else candidate / CONFIG_NAME

    if not config_path.exists():
        raise FileNotFoundError(
            f"No {CONFIG_NAME} found at {config_path}. Run 'zddv init' first."
        )

    with config_path.open("rb") as f:
        raw = tomllib.load(f)

    project = raw.get("project", {})
    simulator = raw.get("simulator", {})
    sources = raw.get("sources", {})
    run = raw.get("run", {})

    return ProjectConfig(
        root=config_path.parent,
        name=project.get("name", config_path.parent.name),
        top=simulator.get("top", "tb_top"),
        simulator=simulator.get("backend", "verilator"),
        rtl=list(sources.get("rtl", [])),
        tb=list(sources.get("tb", [])),
        build_dir=run.get("build_dir", ".zddv/build"),
        run_dir=run.get("run_dir", ".zddv/runs"),
        waveform=bool(run.get("waveform", True)),
    )


def save_project(config: ProjectConfig) -> None:
    def q(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def arr(values: list[str]) -> str:
        return "[" + ", ".join(q(v) for v in values) + "]"

    text = f"""[project]
name = {q(config.name)}

[simulator]
backend = {q(config.simulator)}
top = {q(config.top)}

[sources]
rtl = {arr(config.rtl)}
tb = {arr(config.tb)}

[run]
build_dir = {q(config.build_dir)}
run_dir = {q(config.run_dir)}
waveform = {str(config.waveform).lower()}
"""
    config.config_path.write_text(text, encoding="utf-8")


def initialize_project(path: str | Path) -> ProjectConfig:
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)

    config = ProjectConfig(root=root, name=root.name)
    if config.config_path.exists():
        raise FileExistsError(f"{config.config_path} already exists.")

    (root / "rtl").mkdir(exist_ok=True)
    (root / "tb").mkdir(exist_ok=True)
    save_project(config)
    return config

from pathlib import Path

from zddv.config import initialize_project, load_project, save_project


def test_initialize_and_load_project(tmp_path: Path):
    root = tmp_path / "demo"
    created = initialize_project(root)

    assert created.config_path.exists()
    assert (root / "rtl").is_dir()
    assert (root / "tb").is_dir()

    loaded = load_project(root)
    assert loaded.name == "demo"
    assert loaded.simulator == "verilator"
    assert loaded.top == "tb_top"


def test_source_discovery(tmp_path: Path):
    root = tmp_path / "demo"
    config = initialize_project(root)
    (root / "rtl" / "a.sv").write_text("module a; endmodule\n", encoding="utf-8")
    (root / "tb" / "tb.sv").write_text("module tb; endmodule\n", encoding="utf-8")

    config.rtl = ["rtl/*.sv"]
    config.tb = ["tb/*.sv"]
    save_project(config)

    loaded = load_project(root)
    assert [p.name for p in loaded.source_files()] == ["a.sv", "tb.sv"]

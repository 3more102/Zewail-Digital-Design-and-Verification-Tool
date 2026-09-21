from pathlib import Path

from zddv.regression import load_regression


def test_load_regression(tmp_path: Path):
    path = tmp_path / "regression.toml"
    path.write_text(
        """
[regression]
name = "smoke"
jobs = 2

[[tests]]
name = "basic"
seeds = [1, 2, 3]
timeout_s = 5
plusargs = ["+FOO=1"]
""".strip(),
        encoding="utf-8",
    )

    spec = load_regression(path)

    assert spec.name == "smoke"
    assert spec.jobs == 2
    assert len(spec.cases) == 1
    assert spec.cases[0].name == "basic"
    assert spec.cases[0].seeds == (1, 2, 3)
    assert spec.cases[0].plusargs == ("+FOO=1",)
    assert spec.cases[0].timeout_s == 5.0

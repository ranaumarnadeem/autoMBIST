"""The memory models the hardware integration tests simulate against.

tests/hardware/<memory_name>.v holds hand-written models with planted defects,
faulty spares and the like. `simulate` never picks them up by itself (#44): a
test that is written against one passes it, through ``fixture_model``.
"""
from __future__ import annotations

from pathlib import Path

import yaml

HARDWARE = Path(__file__).resolve().parent / "hardware"


def fixture_model(module_outdir: Path) -> list[Path]:
    """``tests/hardware/<memory_name>.v`` for the module generated in
    ``module_outdir``, as ``run_simulation``'s ``memory_model``."""
    snapshot = yaml.safe_load((Path(module_outdir) / "config.yml").read_text(encoding="utf-8"))
    model = HARDWARE / f"{snapshot['memory_name']}.v"
    assert model.is_file(), f"no test fixture model {model}"
    return [model]

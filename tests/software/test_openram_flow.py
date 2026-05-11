from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autombist.openram_flow import (
    OpenRAMConfigError,
    build_openram_command_args,
    load_openram_config,
)


def test_load_openram_config_rejects_invalid_tech(tmp_path: Path) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text(
        yaml.safe_dump({"tech": "invalid-tech", "word_size": 8, "num_words": 16}, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(OpenRAMConfigError, match="tech must be one of"):
        load_openram_config(config_path)


def test_build_openram_command_resolves_relative_paths(tmp_path: Path) -> None:
    config_path = tmp_path / "openram.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "tech": "scn4m_subm",
                "word_size": 8,
                "num_words": 16,
                "num_rw_ports": 1,
                "num_r_ports": 0,
                "num_w_ports": 0,
                "output_root": "input",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config = load_openram_config(config_path)
    cmd = build_openram_command_args(config, config_path)

    output_root_index = cmd.index("--output-root") + 1
    assert cmd[output_root_index] == str((tmp_path / "input").resolve())

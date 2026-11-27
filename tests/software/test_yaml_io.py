"""The loader for hand-written YAML (openram.yml, harden.yml, yield and rates
files): duplicate keys refused, YAML 1.2 floats, and `<<:` merge keys as
PyYAML's safe loader reads them."""
from __future__ import annotations

import pytest
import yaml

from autombist.yaml_io import load_user_yaml


def test_a_merge_key_is_read_like_safe_load() -> None:
    text = "base: &b\n  word_size: 8\n  tech: sky130\nmine:\n  <<: *b\n  num_words: 16\n"
    assert load_user_yaml(text) == yaml.safe_load(text)
    assert load_user_yaml(text)["mine"] == {"word_size": 8, "tech": "sky130", "num_words": 16}


def test_a_mapping_own_key_overrides_a_merged_one() -> None:
    text = "base: &b {word_size: 8, num_words: 16}\nmine:\n  <<: *b\n  word_size: 32\n"
    assert load_user_yaml(text)["mine"] == {"word_size": 32, "num_words": 16}


def test_a_merge_list_follows_safe_load_precedence() -> None:
    text = "a: &a {x: 1}\nb: &b {x: 2, y: 3}\nmine:\n  <<: [*a, *b]\n"
    assert load_user_yaml(text)["mine"] == yaml.safe_load(text)["mine"]


def test_an_own_key_written_twice_is_still_refused_beside_a_merge() -> None:
    text = "base: &b {word_size: 8}\nmine:\n  <<: *b\n  num_words: 16\n  num_words: 32\n"
    with pytest.raises(yaml.YAMLError, match="duplicate key 'num_words'"):
        load_user_yaml(text)

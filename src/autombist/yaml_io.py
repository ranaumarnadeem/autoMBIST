"""Loading the YAML files a user writes by hand (openram.yml, harden.yml, a
yield-sweep config, a safety-report rates file).

PyYAML's safe loader keeps the last of two equal keys without a word, and as a
YAML 1.1 reader takes ``1e-9`` (no dot) for a string. Both bite in hand-written
files: a key given twice silently loses its first value, and a failure rate
written the way ``json.dumps`` writes it is rejected as not a number (#60, #61).
config.yml has its own loader in generator.py with the same duplicate guard.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class _UserYamlLoader(yaml.SafeLoader):
    """SafeLoader that refuses a repeated key and reads YAML 1.2 floats."""


# A copy, so the resolver added below does not change yaml.SafeLoader itself.
_UserYamlLoader.yaml_implicit_resolvers = {
    first: list(resolvers) for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_UserYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(r"^[-+]?(?:[0-9][0-9_]*)(?:\.[0-9_]*)?[eE][-+]?[0-9]+$"),
    list("-+0123456789"),
)


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UserYamlLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def describe_yaml_error(exc: yaml.YAMLError) -> str:
    """A YAML error on one line: what went wrong and where. PyYAML's own text
    spans several lines with a source excerpt."""
    problem = getattr(exc, "problem", None) or getattr(exc, "context", None) or str(exc).splitlines()[0]
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    return f"{problem} (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else problem


def load_user_yaml(source: Path | str) -> Any:
    """A hand-written YAML file (a Path) or text (a str). Raises yaml.YAMLError,
    a duplicate key included, and OSError for an unreadable file."""
    text = source.read_text(encoding="utf-8") if isinstance(source, Path) else source
    return yaml.load(text, Loader=_UserYamlLoader)  # noqa: S506 -- a SafeLoader subclass

"""Sphinx configuration for the autoMBIST documentation site."""
import os
import sys

sys.path.insert(0, os.path.abspath("../../src"))

project = "autoMBIST"
copyright = "2026, Rana Umar Nadeem"
author = "Rana Umar Nadeem"

try:
    from autombist import __version__ as release
except ImportError:
    release = "1.1.2"
version = release

extensions = [
    "myst_parser",
    "sphinx_design",
    "sphinx_copybutton",
    "sphinxcontrib.mermaid",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
]

source_suffix = {
    ".md": "markdown",
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_title = "autoMBIST"
html_static_path = []

html_theme_options = {
    "source_repository": "https://github.com/ranaumarnadeem/autoMBIST",
    "source_branch": "main",
    "source_directory": "docs/source/",
}

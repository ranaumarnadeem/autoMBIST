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
    "sphinx_sitemap",
    "sphinxext.opengraph",
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

# --------------------------------------------------------------------------
# SEO/AEO — none of this changes page content or layout; it's either a
# separate file crawlers fetch directly (sitemap.xml, robots.txt, llms.txt)
# or invisible <head> metadata that only "activates" for a search engine
# indexing the page or a platform generating a link-preview card.
# --------------------------------------------------------------------------
_SITE_URL = "https://ranaumarnadeem.github.io/autoMBIST/"
_DESCRIPTION = (
    "autoMBIST: an open-source, OpenRAM-integrated MBIST + BIRA + BISR "
    "generator and march-algorithm research platform, proven through open "
    "RTL-to-GDS closure on sky130."
)

# sphinx-sitemap: emits sitemap.xml at the site root; needs html_baseurl set.
# sitemap_url_scheme "{link}" disables the default /en/<version>/ prefix
# (meant for versioned/localized ReadTheDocs-style sites) -- this site is a
# flat single-version deploy, so pages live at e.g. /quickstart.html, not
# /en/1.1.2/quickstart.html. Without this override the sitemap points at
# URLs that don't exist on the deployed site.
html_baseurl = _SITE_URL
sitemap_url_scheme = "{link}"

# sphinxext-opengraph: Open Graph + Twitter Card <head> tags per page, so
# links shared on social/Slack/Discord render a title+description instead
# of a bare URL. It auto-extracts each page's description from its own
# first paragraph and OVERWRITES Sphinx's own html_meta "description" tag
# with that same (truncated-to-ogp_description_length) text -- confirmed by
# testing, not assumed: a plain html_meta "description" override here is
# silently shadowed on every page. So there's one length knob, not a custom
# per-page description; 320 was tuned against index.md's real intro
# paragraph specifically (~305 chars) to avoid cutting mid-sentence.
ogp_site_url = _SITE_URL
ogp_site_name = "autoMBIST"
ogp_type = "website"
ogp_description_length = 280
ogp_custom_meta_tags = [
    '<meta name="twitter:card" content="summary" />',
]

# robots.txt + llms.txt: copied verbatim to the site root (not part of any
# page's visible nav — only fetched directly by crawlers/answer engines).
html_extra_path = ["_extra"]

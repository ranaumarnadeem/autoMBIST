from __future__ import annotations

from .fault_gen import generate_fault_files, generate_fault_masks, write_fault_files
from .generator import ConfigError, generate_from_config, load_config

__version__ = "0.3.0"

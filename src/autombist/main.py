from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autombist.cli import app


def main() -> None:
    """The `autombist` command. A file or OS error that no command caught is
    still one `autombist:` line and exit 1, not a traceback (#59); every
    command's own error handling runs first."""
    try:
        app()
    except OSError as exc:
        print(f"autombist: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

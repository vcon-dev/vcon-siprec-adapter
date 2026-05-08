"""Console-script entry point for the `siprec-srs` command.

The repo's `main.py` lives at the repo root (intentionally — it needs to
be runnable as `python main.py`). This module provides a packaged
re-export so `pip install` can wire up a console script.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def run() -> None:
    """Entry point for `siprec-srs` console script."""
    # Ensure the repo root is on sys.path so `import main` resolves.
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from main import main  # type: ignore[import-not-found]

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()

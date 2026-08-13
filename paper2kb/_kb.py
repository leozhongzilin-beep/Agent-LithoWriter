"""Bootstrap so the `kb` package (literature_kb) is importable from paper2kb.

literature_kb lives next to paper2kb under writing-agent/; it is not pip
installed, so we add its directory to sys.path before importing `kb`. The KB's
validators (kb.package.validate_package) are the canonical quality gates the
skill reuses — no duplicated validation logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LITERATURE_KB = Path(__file__).resolve().parent.parent / "literature_kb"


def ensure_kb_importable() -> None:
    if str(_LITERATURE_KB) not in sys.path:
        sys.path.insert(0, str(_LITERATURE_KB))


def kb_package():
    """Return the `kb.package` module (imported lazily after bootstrap)."""
    ensure_kb_importable()
    from kb import package

    return package

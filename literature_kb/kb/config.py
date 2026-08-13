"""KB_ROOT resolution for the Literature Knowledge Base.

Precedence (highest wins):
    1. explicit --root CLI flag (passed in by cli.py)
    2. KB_ROOT environment variable
    3. default: <literature_kb>/data  (derived from this file's location)

The KB is fully self-contained: every paper's canonical package, manifest and
archived source live under KB_ROOT. Nothing points outside the KB.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_KB_ROOT = Path(__file__).resolve().parent.parent / "data"

# Sub-directories of KB_ROOT (all created by `kb init`).
SUBDIRS = ("raw", "vectors")

# Canonical archive file names inside raw/<paper_id>/.
ARCHIVE_PACKAGE = "package.json"
ARCHIVE_MANIFEST = "manifest.json"
ARCHIVE_SOURCE_DIR = "source"

# SQLite file name.
DB_FILE = "kb.db"


def resolve_kb_root(explicit: str | Path | None = None) -> Path:
    """Return the KB_ROOT as an absolute, normalized Path."""
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("KB_ROOT")
    if env and env.strip():
        return Path(env).expanduser().resolve()
    return DEFAULT_KB_ROOT.resolve()


def db_path(root: Path) -> Path:
    """Path to the SQLite database file under a KB_ROOT."""
    return root / DB_FILE


def paper_dir(root: Path, paper_id: str) -> Path:
    """raw/<paper_id>/ — canonical package + manifest + source archive."""
    return root / "raw" / paper_id


def source_dir(root: Path, paper_id: str) -> Path:
    """raw/<paper_id>/source/ — archived original document(s)."""
    return paper_dir(root, paper_id) / ARCHIVE_SOURCE_DIR

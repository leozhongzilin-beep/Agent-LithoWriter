"""Literature Knowledge Base — storage tools.

Self-contained storage layer for the hierarchical literature KB (spec v1.0):
SQLite (kb.db) for relational metadata + a JSON package archive under
data/raw/<paper_id>/. Fully decoupled from any external paper vault.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import resolve_kb_root
from .store import KBStore

__all__ = ["KBStore", "__version__", "resolve_kb_root"]

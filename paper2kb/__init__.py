"""paper2kb — Paper-to-Literature-KB skill (spec Paper_to_Literature_KB_Skill_v1.0).

Processes a paper source (PDF / markdown / XML / LaTeX / text) into the
canonical package dict that `kb add` ingests: L0-L4 + formulas + citation
metadata + citation graph + a validation report. LLM-driven, per-layer calls,
with the spec's no-fabrication rules enforced in the prompts and re-checked by
validation.
"""

from __future__ import annotations

__version__ = "0.1.0"

# process_paper is exposed once the pipeline module lands (lazy import keeps
# the package importable as modules are added incrementally).

__all__ = ["__version__"]

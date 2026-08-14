"""Writing Agent - an autonomous academic paper writing agent.

A self-contained Python program that turns a research topic or narrative
into a structured LaTeX paper, using a built-in review loop for iterative
improvement.

Pipeline:
    Phase 1  plan    - parse input -> claims-evidence matrix -> PAPER_PLAN.md
    Phase 2  write   - section-by-section LaTeX generation
    Phase 3  review  - autonomous review loop (review -> fix -> re-review)
    Phase 4  finalize- bib cleanup, format checks, final report

Run from the command line:
    python -m write_agent.cli --topic "..."
    python -m write_agent.cli --narrative NARRATIVE_REPORT.md
"""

__version__ = "0.1.0"

"""SQLite DDL for the Literature Knowledge Base.

Projection of the v1 spec's recommended collections (§20) plus two helper
tables. Identity fields (paper_id / doi / source_hash / citation_key /
bibtex_key) are deliberately separate columns — that separation is a frozen
invariant of this KB and must never be merged.

Conventions:
    - JSON fields are stored as TEXT (json.dumps). Serialization helpers live
      in store.py.
    - status / claim_type / evidence_type / relation columns use CHECK
      constraints to pin the spec's enum sets.
    - WAL mode + foreign keys are enabled per-connection in store.py.
"""

from __future__ import annotations

SCHEMA_VERSION = "1.0"

# The 18 spec collections + sequences (counter) + one index helper table.
TABLES = (
    "papers", "paper_cards", "paper_methods", "paper_metrics",
    "paper_comparisons", "paper_claims", "paper_evidence", "paper_fulltext",
    "formulas", "formula_variables", "concepts", "metrics_ontology",
    "citation_records", "citation_styles", "citation_graph", "embeddings",
    "processing_jobs", "validation_reports", "sequences",
)

DDL = """

-- ---------------------------------------------------------------------------
-- L0 + identity + provenance
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS papers (
    paper_id            TEXT PRIMARY KEY,          -- {DOMAIN}_{YEAR}_{NNN}
    title               TEXT NOT NULL,
    one_line_description TEXT,
    authors_summary     TEXT,
    year                INTEGER,
    venue               TEXT,
    article_type        TEXT,
    doi                 TEXT UNIQUE,               -- external canonical identity
    url                 TEXT,
    keywords            TEXT,                      -- JSON [str]
    domain_tags         TEXT,                      -- JSON [str]
    method_tags         TEXT,                      -- JSON [str]
    bibliographic_record TEXT,                     -- JSON {authors[], title, container_title, ...}
    citation_key        TEXT NOT NULL UNIQUE,      -- writing-agent internal reference
    bibtex_key          TEXT,                      -- lowercase BibTeX key (separate!)
    citation_cache      TEXT,                      -- JSON {bibtex, ieee, nature, custom} (rebuildable)
    source_hash         TEXT,                      -- integrity / change detection
    source_path         TEXT,                      -- archived source pointer
    source_type         TEXT,                      -- pdf | xml | html | latex | md | unknown
    source_reachable    INTEGER DEFAULT 1,         -- 0 = archived source missing
    processor_name      TEXT,
    processor_version   TEXT,
    package_spec_version TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- L1 — Paper Understanding
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_cards (
    paper_id            TEXT PRIMARY KEY REFERENCES papers(paper_id),
    abstract            TEXT,
    research_problem    TEXT,
    research_gap        TEXT,
    main_idea           TEXT,
    method_summary      TEXT,
    main_contributions  TEXT,                      -- JSON [str]
    innovation          TEXT,
    key_findings_summary TEXT,
    limitations         TEXT,                      -- JSON [str]
    datasets_summary    TEXT,
    methods_summary     TEXT,
    recommended_use     TEXT                       -- JSON {background: none|weak|moderate|strong, ...}
);

-- ---------------------------------------------------------------------------
-- L2-A — Method Card
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_methods (
    method_id           TEXT PRIMARY KEY,          -- {paper_id}.md001
    paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
    method_name         TEXT,
    method_family       TEXT,
    task                TEXT,
    input               TEXT,
    output              TEXT,
    architecture        TEXT,
    algorithm           TEXT,
    optimization        TEXT,
    loss_function       TEXT,
    training_strategy   TEXT,
    inference_strategy  TEXT,
    iterative_or_direct TEXT,
    system_context      TEXT                       -- JSON {technology_node, wavelength, NA, ...}
);

-- ---------------------------------------------------------------------------
-- L2-B — Metric Object (every reported number must carry condition/evidence)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_metrics (
    metric_id           TEXT PRIMARY KEY,          -- {paper_id}.mt012
    paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
    name                TEXT NOT NULL,
    value               REAL,                      -- null when not reported / textual
    value_text          TEXT,                      -- e.g. "2.1–2.5", "N/A", original string
    unit                TEXT,
    status              TEXT NOT NULL
                        CHECK (status IN ('reported','not_reported','not_applicable','unclear')),
    agg_type            TEXT,                      -- mean | best | max | worst | per_case | unknown
    condition           TEXT,                      -- JSON {dataset, pattern, pitch, wavelength, NA, ...}
    baseline            TEXT,
    source_evidence_id  TEXT,
    source_page         TEXT,
    source_section      TEXT,
    confidence          REAL
);

-- ---------------------------------------------------------------------------
-- L2 — Comparison Object
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_comparisons (
    comparison_id       TEXT PRIMARY KEY,          -- {paper_id}.cm002
    paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
    metric              TEXT,
    condition           TEXT,                      -- JSON
    baseline            TEXT,
    proposed            TEXT,
    improvement         TEXT,
    comparison_validity TEXT
                        CHECK (comparison_validity IN ('comparable','partially_comparable','not_comparable')),
    source_evidence_id  TEXT
);

-- ---------------------------------------------------------------------------
-- L3 — Claims
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_claims (
    claim_id            TEXT PRIMARY KEY,          -- {paper_id}.cl005
    paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
    claim               TEXT NOT NULL,
    claim_type          TEXT
                        CHECK (claim_type IN ('definition','methodological','quantitative',
                                              'comparative','causal','limitation','conclusion')),
    strength            TEXT,                      -- A | B | C | D
    supporting_evidence_ids TEXT,                  -- JSON [str]
    confidence          REAL
);

-- ---------------------------------------------------------------------------
-- L3 — Evidence
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_evidence (
    evidence_id         TEXT PRIMARY KEY,          -- {paper_id}.ev007
    paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
    section             TEXT,
    subsection          TEXT,
    page                TEXT,
    paragraph_index     INTEGER,
    figure_ref          TEXT,
    table_ref           TEXT,
    source_text         TEXT NOT NULL,             -- verbatim from the original
    claim               TEXT,
    evidence_type       TEXT,                      -- definition | methodological_statement | observation | experimental_result | comparison | limitation | causal_claim | quantitative_result
    metric_refs         TEXT,                      -- JSON [str]
    formula_refs        TEXT,                      -- JSON [str]
    supports_claim_ids  TEXT,                      -- JSON [str]
    confidence          REAL
);

-- ---------------------------------------------------------------------------
-- L4 — Full-text (pointer now; paragraph chunks land here later)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_fulltext (
    paper_id            TEXT PRIMARY KEY REFERENCES papers(paper_id),
    fulltext_pointer    TEXT,                      -- path to archived source copy in raw/<id>/source/
    section_index       TEXT,                      -- JSON [str] — reserved
    chunk_available     INTEGER DEFAULT 0,         -- 0 until paragraph chunking lands
    chunks              TEXT                       -- JSON — reserved
);

-- ---------------------------------------------------------------------------
-- Formula KB
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS formulas (
    formula_id          TEXT PRIMARY KEY,          -- {paper_id}.fm003
    paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
    section             TEXT,
    page                TEXT,
    formula_latex       TEXT NOT NULL,
    formula_role        TEXT,                      -- objective | loss | forward_model | constraint | regularization | metric | physical_model | evaluation | network | update_rule
    semantic_description TEXT,
    variables           TEXT,                      -- JSON [str] formula_variables ids (denormalized convenience)
    application         TEXT,
    assumptions         TEXT,
    related_formulas    TEXT,                      -- JSON [str]
    reusability         TEXT,                      -- JSON {directly_reusable: bool, requires_context: bool}
    notation_dependencies TEXT,                    -- JSON
    source_evidence_id  TEXT,
    confidence          REAL
);

CREATE TABLE IF NOT EXISTS formula_variables (
    variable_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    formula_id          TEXT NOT NULL REFERENCES formulas(formula_id),
    symbol              TEXT NOT NULL,
    meaning             TEXT,                      -- "unclear" when unknown — never guessed
    unit                TEXT
);

-- ---------------------------------------------------------------------------
-- Concept / Metric Ontology (curated; schema + seed hook only in v1)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS concepts (
    concept_id          TEXT PRIMARY KEY,
    canonical_name      TEXT NOT NULL,
    aliases             TEXT,                      -- JSON [str]
    parent_concepts     TEXT,                      -- JSON [str]
    child_concepts      TEXT,                      -- JSON [str]
    related_concepts    TEXT,                      -- JSON [str]
    related_methods     TEXT,                      -- JSON [str]
    related_papers      TEXT                       -- JSON [str]
);

CREATE TABLE IF NOT EXISTS metrics_ontology (
    metric_name         TEXT PRIMARY KEY,
    canonical_definition TEXT,
    aliases             TEXT,                      -- JSON [str]
    unit                TEXT,
    category            TEXT,
    measurement_scope   TEXT,
    comparability_rules TEXT,
    common_pitfalls     TEXT
);

-- ---------------------------------------------------------------------------
-- Citation Architecture (three-layer separation: record / key / rendering)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS citation_records (
    record_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
    citation_key        TEXT NOT NULL,
    style_id            TEXT NOT NULL,
    in_text_citation    TEXT,
    bibliography_entry  TEXT,
    reference_number    INTEGER,
    style_source        TEXT,
    style_version       TEXT,
    UNIQUE (paper_id, style_id)
);

CREATE TABLE IF NOT EXISTS citation_styles (
    style_id            TEXT PRIMARY KEY,
    name                TEXT,
    csl_path            TEXT,
    version             TEXT,
    description         TEXT
);

CREATE TABLE IF NOT EXISTS citation_graph (
    edge_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_paper        TEXT NOT NULL REFERENCES papers(paper_id),
    target_paper        TEXT NOT NULL REFERENCES papers(paper_id),
    relation            TEXT NOT NULL
                        CHECK (relation IN ('cites','extends','improves','compares_with',
                                            'uses','criticizes','builds_on','same_method_family')),
    confidence          REAL,
    source_evidence_id  TEXT,
    UNIQUE (source_paper, target_paper, relation)
);

-- ---------------------------------------------------------------------------
-- Vector index (reserved — no tooling in v1)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS embeddings (
    embed_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id            TEXT REFERENCES papers(paper_id),
    object_type         TEXT,                      -- paper | evidence | formula | metric | ...
    object_id           TEXT,
    model               TEXT,
    model_version       TEXT,
    vector              BLOB,
    created_at          TEXT
);

-- ---------------------------------------------------------------------------
-- Provenance / audit
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processing_jobs (
    job_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id            TEXT,
    action              TEXT,                      -- init | import | replace | resolve
    decision            TEXT,                      -- INSERTED | UPDATED | SKIPPED_DUPLICATE | FAILED
    warnings            TEXT,                      -- JSON [str]
    source_hash_before  TEXT,
    source_hash_after   TEXT,
    processor_name      TEXT,
    processor_version   TEXT,
    counts              TEXT,                      -- JSON {table: row_count}
    imported_from       TEXT,                      -- path of the package file
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_reports (
    report_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
    gates               TEXT,                      -- JSON {QG-1: bool, QG-2: bool, ...}
    pass                INTEGER,
    warnings            TEXT,                      -- JSON [str]
    created_at          TEXT
);

-- ---------------------------------------------------------------------------
-- paper_id counter — per (domain, year), never MAX+1 (breaks after deletes)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sequences (
    domain              TEXT NOT NULL,
    year                INTEGER NOT NULL,
    next_value          INTEGER NOT NULL,
    PRIMARY KEY (domain, year)
);

"""


def create_schema(conn) -> None:
    """Create all tables. Idempotent — safe to run on an existing KB."""
    conn.executescript(DDL)
    conn.commit()


def list_tables(conn):
    """Return the table names currently present in the database."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]

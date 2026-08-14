"""Prompt templates for the writing agent.

These prompts encode a paper-writing methodology:
    - five-sentence abstract formula (Farquhar)
    - introduction structure (hook -> gap -> approach -> bullets -> preview)
    - claims-evidence matrix
    - sentence-level clarity principles (Gopen & Swan)
    - 5-pass scientific writing quality audit
    - reviewer independence (zero-context review each round)
"""

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_PLANNER = """You are an expert academic paper planner. You help structure
research material into a rigorous, evidence-backed paper outline. You follow
the principle that a paper is a short, rigorous, evidence-backed technical
story. Every claim must map to evidence; every experiment must support a claim.
You write in {language}. Target venue: {venue} ({max_pages} pages main body,
references and appendix excluded for ML venues)."""

SYSTEM_WRITER = """You are an expert academic LaTeX writer for {venue}
conference/journal papers. You write in {language}. You follow rigorous
scientific writing methodology:

1. Seven-sentence abstract formula (for general/SPIE papers; ICLR/NeurIPS may
   use 5-sentence variant):
   (a) field-level context — 1 sentence on the broader research area.
   (b) specific background — 1 sentence on the sub-problem.
   (c) gap / limitations of prior work — 1 sentence stating the unsolved problem.
   (d-e) proposed approach — 2-3 sentences describing your method, key
         innovation, and how it differs from prior work.
   (f) quantitative results — 1-2 sentences with concrete numbers (PE reduction,
       SSIM, runtime, etc.).  Never bury the strongest number.
   (g) significance / broader impact — 1 sentence on why this matters.

2. Introduction: opening hook, explicit gap ("However, ..."), approach
   overview, 2-4 specific falsifiable contribution bullets, early results
   preview, optional roadmap. Method must start by page 2-3.
   IMPORTANT: each \\cite{{...}} must contain ≤3 keys.  Never stack >3
   citations in a single \\cite command.  If you need to cite many related
   works, spread them across multiple \\cite calls with contextual phrases
   ("data-driven approaches \\cite{{a,b,c}} have been extended by level-set
   methods \\cite{{d}} and L2O frameworks \\cite{{e,f}}...").

3. Sentence-level clarity: keep subject and verb close, put important info at
   the end, context at the start, old->new flow, one unit one function, put
   actions in verbs, set the stage before new material.

4. No AI-isms: never use "delve", "pivotal", "landscape", "tapestry",
   "underscore", "noteworthy", "intriguingly". Avoid "It is worth noting
   that", "It is important to note that". Strip clutter: "due to the fact
   that" -> "because", "in order to" -> "to".

5. Terminology consistency (the Banana Rule): never rename the same concept.

6. No fabricated content. No [VERIFY] markers left unresolved. No TODO/FIXME.

7. Write complete sections, not outlines. Output compilable LaTeX.

8. Figure guidelines: every figure MUST be followed by a four-part discussion
   paragraph (background → data presentation → mechanism analysis → concluding
   sentence).  The concluding sentence is normal body text — never wrap it in
   \\textbf{{Summary:}} or any other label.  Set figure width to
   0.75\\columnwidth with keepaspectratio.  Use float placement [!htb].

9. Citation hygiene: the final compiled paper must use exactly 30 unique
   references.  Every \\cite{{...}} call must match an entry in references.bib,
   and every references.bib entry must appear in at least one \\cite call.
   No dead bib entries, no undefined citations (orphan-free)."""

SYSTEM_REVIEWER = """You are a senior adversarial academic reviewer
(NeurIPS/ICML area-chair level). You review papers for {venue}. You start from
the assumption that the work is broken somewhere, and your job is to find
where. You are brutally honest. You judge ONLY from the artifact you are given
-- you have no prior knowledge of the author's intent, and you do not care
what the author believes they fixed. You write in {language}.

In addition to your standard review, you MUST check the following
mechanical/formatting rules and flag any violations as MAJOR weaknesses:

1. HYPERREF CONFIG: main.tex must use
   \\usepackage[draft=false,hidelinks]{{hyperref}}, NOT a long list of
   colorlinks/linkbordercolor options.  Violation = MAJOR.
2. ABSTRACT STRUCTURE: must follow 7-sentence flow: field background →
   specific background → gap → method part 1 → method part 2 → quantitative
   results → significance.  Violation (e.g. jumping straight to "We
   propose...") = MAJOR.
3. FIGURE DISCUSSIONS: every figure must be followed by a four-part
   discussion (background → data → mechanism → concluding sentence).
   Figures with only a caption and one-line mention = MAJOR.
4. NO SUMMARY LABEL: no paragraph may begin with \\textbf{{Summary:}}.
   Violation = MAJOR.
5. FIGURE SIZE + PLACEMENT: all \\includegraphics must use
   width=0.75\\columnwidth,keepaspectratio (or smaller) and float spec
   [!htb].  Full-width figures or [!t]-only floats that cause "Text page
   contains only floats" = MAJOR.
6. CITATION DENSITY: every \\cite{{...}} call must contain ≤3 keys.
   Any \\cite with >3 keys = CRITICAL.
7. REFERENCE COUNT: the paper must use exactly 30 unique citation keys.
   Fewer or more = MAJOR.
8. ORPHAN BIB ENTRIES: every entry in references.bib must be \\cite'd at
   least once in the body; every \\cite key must resolve to a bib entry.
   No dead entries, no undefined citations.  Violation = MAJOR."""

SYSTEM_FIXER = """You are an expert academic paper revision specialist. You
apply reviewer feedback to improve a LaTeX paper. You follow the same writing
methodology as the writer: sentence-level clarity, terminology consistency,
no AI-isms, no overclaiming. You only make changes that the reviewer explicitly
asked for, plus the minimal direct consequences of those changes. You never
fabricate experiments, numbers, or citations. You write in {language}.

CRITICAL FORMAT RULES you must respect when applying fixes:
- hyperref in main.tex: \\usepackage[draft=false,hidelinks]{{hyperref}}
- figure width: 0.75\\columnwidth,keepaspectratio
- figure float: [!htb]
- citations: ≤3 keys per \\cite{{...}}
- references: exactly 30 unique keys, no orphans (no dead bib entries, no undefined cites)
- no \\textbf{{Summary:}} labels anywhere"""

SYSTEM_FINALIZER = """You are an academic paper quality auditor. You run the
final scientific writing quality audit (5 passes: clutter extraction, active
voice, sentence architecture, keyword consistency, numerical and citation
integrity) and report issues without fixing them. You write in {language}."""


# ---------------------------------------------------------------------------
# Phase 1: Planning
# ---------------------------------------------------------------------------

PLAN_EXTRACT_CLAIMS = """Read the following research material and extract the
core claims, evidence, and framing.

RESEARCH MATERIAL:
{input_text}

Return a JSON object with exactly these fields:
{{
  "one_sentence_contribution": "the single sentence that best states the paper's core takeaway",
  "title": "a working title (specific and informative, not generic)",
  "claims": [
    {{"claim": "specific falsifiable claim", "evidence": "which experiments/metrics/results support it", "status": "supported|partially_supported|needs_experiment", "section": "suggested section"}}
  ],
  "key_weaknesses": ["known weaknesses or gaps"],
  "suggested_framing": "how the paper should position itself"
}}

Guidelines:
- Claims must be specific and falsifiable. "We study problem X" is not a claim.
- "We improve method A by 15% on benchmark C" is a claim.
- If the material contains experiment numbers, preserve them exactly.
- If a claim lacks evidence, mark it needs_experiment honestly.
"""

PLAN_BUILD_OUTLINE = """You are building the paper outline. Use the claims-evidence
matrix and framing below.

ONE-SENTENCE CONTRIBUTION: {contribution}
TITLE: {title}
CLAIMS-EVIDENCE MATRIX:
{claims_matrix}
KEY WEAKNESSES: {weaknesses}
FRAMING: {framing}

Target venue: {venue} ({max_pages} pages main body).
Page budget must sum to {max_pages} pages.

Design {min_sections}-{max_sections} sections. Choose a paper type
(empirical / theory / method) and structure accordingly.

Return a JSON object with exactly this structure:
{{
  "paper_type": "empirical|theory|method",
  "sections": [
    {{
      "id": "1",
      "title": "Introduction",
      "filename": "1_introduction.tex",
      "purpose": "one line on what this section does",
      "key_points": ["bullet points to cover"],
      "claims": ["claim indices from the matrix this section supports"],
      "citations_hint": ["topic hints for citations, e.g. 'transformers attention'"],
      "target_pages": 1.5,
      "figures": ["description of figures/tables for this section, if any"]
    }}
  ],
  "figure_plan": [
    {{"id": "fig1", "type": "hero|architecture|plot|table", "description": "what it shows and what comparison it demonstrates", "data_source": "where data comes from"}}
  ],
  "citation_plan": {{
    "intro": ["exact paper titles to cite, e.g. 'Attention Is All You Need (Vaswani et al., 2017)'"],
    "related": ["exact paper titles by category"],
    "method": ["exact paper titles"]
  }}
}}

Rules:
- Every claim from the matrix must be assigned to at least one section.
- The abstract is section 0 (id "0", filename "0_abstract.tex", title "Abstract").
- Related work must be at least 1 full page and organized by category.
- The hero figure (Figure 1) must be described in detail with its comparison.
- Front-load the contribution: title, abstract, introduction, hero figure.
- Citation hints must be EXACT paper titles (not vague topics), optionally with
  "(First Author et al., YEAR)" -- the citation verifier matches on the title.
"""


# ---------------------------------------------------------------------------
# Phase 2: Writing
# ---------------------------------------------------------------------------

WRITE_ABSTRACT = """Write the ABSTRACT for the paper.

PAPER CONTEXT:
{paper_context}

Use the SEVEN-SENTENCE abstract formula (for SPIE/general papers):
1. Field-level context (1 sentence): the broader research area, e.g.
   "Inverse lithography technology (ILT) has emerged as a powerful
   computational approach for mask design in optical lithography..."
   Do NOT start with the paper's own contribution — set the stage first.
2. Specific background (1 sentence): the sub-problem, e.g. proximity
   lithography and its gap-induced diffraction issues.
3. Gap / limitations of prior work (1 sentence): what existing methods
   cannot do — high cost, local-optima convergence, etc.
4. Proposed approach, part 1 (1 sentence): the main method and how it
   avoids the limitations described above.
5. Proposed approach, part 2 (1 sentence): additional innovations (e.g.
   PAEE objective, regularization) that complement the core method.
6. Quantitative results (1-2 sentences): concrete numbers — PE reduction
   percentages, SSIM/RMSE, runtimes.  Surface the strongest result.
7. Significance / broader impact (1 sentence): why this matters beyond
   the immediate experiments.

Constraints:
- {min_words}-{max_words} words.
- Self-contained: understandable without reading the paper.
- No \\cite{{}}, no undefined acronyms.
- No "In this paper, we..." (redundant — the abstract IS the paper).
- No \\\\begin{{abstract}} — that lives in main.tex.
- Each sentence must do exactly ONE job. No fused sentences.
- Return ONLY the abstract text, no markdown fences, no preamble.
"""

WRITE_SECTION = """Write the LaTeX content for the section below.

PAPER CONTEXT (title, claims, framing):
{paper_context}

SECTION SPEC:
{section_spec}

RELATED CONTENT ALREADY WRITTEN (for consistency):
{written_so_far}

TARGET: {target_pages} page(s).

Rules:
- Write COMPLETE LaTeX section content (no \\\\section{{...}} command if the
  caller wraps it; if this is the abstract, just the prose).
- Follow the writing methodology: subject/verb close, important info at end,
  no AI-isms, terminology consistent with prior sections, no fabricated
  numbers or citations.
- Related Work: minimum 1 full page, organized by category using
  \\\\paragraph{{Category.}} -- synthesize and compare, not paper-by-paper
  mini-summaries. End each category with how this paper differs.
- Experiments: state the setup, then main results first, then ablations.
  Make explicit what claim each experiment supports.
- Conclusion: rephrase contributions (do not copy-paste), honest limitations,
  1-2 concrete future directions.
- Use \\\\citep{{key}} for citations (ML venues) or \\\\cite{{key}} (IEEE).
  Only use citation keys that exist in the bibliography or are listed in
  CITATION KEYS AVAILABLE below. If none available, leave the citation out
  or use a \\\\citep{{key}} placeholder that will be resolved by the citation
  module (do NOT invent key names -- use the exact key provided).

CITATION KEYS AVAILABLE: {citation_keys}

Return ONLY the LaTeX body text.
"""

WRITE_SECTION_SPECIFIC_INTRO = """Write the INTRODUCTION section.

{paper_context}

SECTION SPEC:
{section_spec}

WRITTEN SO FAR:
{written_so_far}

TARGET: {target_pages} page(s).

Structure:
1. Opening hook: what problem, why does it matter now (1-2 sentences, specific).
2. Gap: "However, ..." -- why prior work is insufficient.
3. Approach overview: what this paper does differently, key insight.
4. Contribution bullets: {num_bullets} specific, falsifiable items
   (no longer than 1-2 lines each). Bad: "We study X", "We perform extensive
   experiments". Good: "We prove X converges in O(n log n) under Y",
   "We introduce Z which reduces memory by 40%".
5. Results preview: surface the strongest result early.
6. Optional roadmap: "The rest of this paper is organized as...".

Constraints:
- Method should start by page 2-3 at the latest, so keep intro ~1-1.5 pages.
- No generic field-background opening.
- End with a clear one-sentence takeaway.

Return ONLY the LaTeX body text (the section command is added by the caller).
"""

WRITE_SECTION_SPECIFIC_RELATED = """Write the RELATED WORK section.

{paper_context}

SECTION SPEC:
{section_spec}

WRITTEN SO FAR:
{written_so_far}

TARGET: {target_pages} page(s).

Rules:
- MINIMUM 1 full page (3-4 substantive paragraphs). Short related work is a
  common reviewer complaint.
- Organize by category using \\\\paragraph{{Category Name.}}
- Organize methodologically, by assumption class, or by research question.
  Do NOT write paper-by-paper mini-summaries.
- Each category: 1 paragraph synthesizing the line of work + 1-2 sentences
  positioning this paper.
- Do NOT just list papers -- synthesize and compare.
- End each category with how this paper relates/differs.
- Only use citation keys from CITATION KEYS AVAILABLE.

CITATION KEYS AVAILABLE: {citation_keys}

KB KNOWN WORK AVAILABLE (from your curated library — cite ONLY keys listed in
CITATION KEYS AVAILABLE; cards whose key is absent may be described but not cited):
{kb_cards}

Return ONLY the LaTeX body text.
"""

WRITE_SECTION_SPECIFIC_METHOD = """Write the METHOD / PRELIMINARIES section.

{paper_context}

SECTION SPEC:
{section_spec}

WRITTEN SO FAR:
{written_so_far}

TARGET: {target_pages} page(s).

Rules:
- Define notation early (reference math_commands.tex macros if appropriate).
- Use \\\\begin{{definition}}, \\\\begin{{theorem}} environments for formal
  statements.
- For theory papers: include proof sketches of key results in main body, full
  proofs in appendix. Include a comparison table of prior bounds vs. this paper.
- Include algorithm pseudocode if applicable.
- Before each equation/theorem, tell the reader why it matters (set the stage
  before new material).
- Keep notation consistent with the rest of the paper.

CITATION KEYS AVAILABLE: {citation_keys}

Return ONLY the LaTeX body text.
"""


# ---------------------------------------------------------------------------
# Phase 3: Review loop
# ---------------------------------------------------------------------------

REVIEW_ROUND1 = """You are reviewing an academic paper for {venue}. This is a
FRESH, ZERO-CONTEXT review. Judge the paper ONLY from what is pasted below.
You have no idea what the author believes they fixed, and you do not care.
Start from the assumption that the paper is broken somewhere.

## Paper (LaTeX source, all sections)
{paper_text}

## Instructions
Please act as a senior ML reviewer ({venue} level). Provide:
1. **Score**: X/10 (6 = weak accept, 7 = accept, top-venue quality)
2. **Summary**: 2-3 sentences
3. **Strengths**: bullet list, ranked
4. **Weaknesses**: bullet list, ranked CRITICAL > MAJOR > MINOR. For each
   CRITICAL/MAJOR weakness give a specific, actionable fix.
5. **Verdict**: one of exactly "ready", "almost", "not ready"

## FORMAT RULES TO AUDIT (flag violations as weaknesses)

**Hyperref (MAJOR if violated):**
main.tex MUST use \\usepackage[draft=false,hidelinks]{{hyperref}}.
Long deprecated option lists (colorlinks=false, linkbordercolor=...) are wrong.

**Abstract structure (MAJOR if violated):**
Must follow 7-sentence flow: field context → specific background → prior-work
gap → method part 1 → method part 2 → quantitative results → significance.
Jumping straight to "We propose..." without field context = violation.

**Figure discussions (MAJOR if violated):**
Every figure (including Fig.1 flowchart) must have a multi-paragraph discussion
after it covering: (a) why this test matters, (b) data from figure/table,
(c) physical/algorithmic mechanism, (d) concluding sentence.  A bare one-line
mention like "Fig. X shows the results." = violation.

**Summary label (MAJOR if violated):**
No paragraph may start with \\textbf{{Summary:}}.  Concluding sentences are
normal body text, not labelled.

**Figure size + float placement (MAJOR if violated):**
All \\includegraphics must use width=0.75\\columnwidth,keepaspectratio (or
smaller).  Float spec must be [!htb], not [!t] alone.  "Text page contains
only floats" warning = violation.

**Citation density (CRITICAL if violated):**
Every \\cite{{...}} call must contain ≤3 keys.  Any \\cite with 4+ keys
stacked together = violation.

**Reference count (MAJOR if violated):**
The paper must cite EXACTLY 30 unique references.  Fewer or more = violation.

**ORPHAN BIB ENTRIES (MAJOR if violated): every entry in references.bib must be
\\cite'd in the body; every \\cite key must resolve to a bib entry.  Dead entries
or undefined citations = violation.**
Every entry in references.bib must be \\cite'd in the body; every \\cite key
must resolve to a bib entry.  Dead entries or undefined citations = violation.

Focus on: theoretical rigor, claims vs evidence alignment, writing clarity,
self-containedness, notation consistency, missing references, overclaiming,
and whether a skim reader could recover the main claim from title + abstract +
introduction + figure descriptions.

Return ONLY a JSON object:
{{
  "score": 6.0,
  "summary": "...",
  "strengths": ["..."],
  "weaknesses": [
    {{"severity": "CRITICAL|MAJOR|MINOR", "issue": "...", "fix": "specific actionable fix", "location": "section or line hint"}}
  ],
  "verdict": "ready|almost|not ready"
}}
"""

REVIEW_ROUND2 = """You are reviewing an academic paper for {venue}. This is a
FRESH, ZERO-CONTEXT review -- ignore any prior reviews. Judge the paper ONLY
from the current state pasted below.

## Paper (LaTeX source, all sections)
{paper_text}

## Instructions
Same format and FORMAT RULES TO AUDIT as Round 1:
1. **Score**: X/10 (6 = weak accept, 7 = accept)
2. **Summary**: 2-3 sentences
3. **Strengths**: bullet list
4. **Weaknesses**: ranked CRITICAL > MAJOR > MINOR, each with a specific fix
   (include format-rule violations from the checklist below)
5. **Verdict**: "ready", "almost", or "not ready"

FORMAT RULES TO AUDIT (same as Round 1):
- hyperref = \\usepackage[draft=false,hidelinks]{{hyperref}} (MAJOR)
- abstract = 7-sentence flow, not starting with "We propose..." (MAJOR)
- figure discussion = 4-part after every figure (MAJOR)
- no \\textbf{{Summary:}} labels (MAJOR)
- figure width = 0.75\\columnwidth, float = [!htb] (MAJOR)
- \\cite ≤3 keys each (CRITICAL)
- exactly 30 unique references, no orphan bib entries (MAJOR)

Be brutally honest. If the paper is genuinely ready, say "ready" clearly.
If it is not, list what still blocks it.

Return ONLY a JSON object with the same schema:
{{
  "score": 6.0,
  "summary": "...",
  "strengths": ["..."],
  "weaknesses": [
    {{"severity": "CRITICAL|MAJOR|MINOR", "issue": "...", "fix": "...", "location": "..."}}
  ],
  "verdict": "ready|almost|not ready"
}}
"""

FIX_PROMPT = """Apply the reviewer feedback below to improve the paper.

REVIEWER FEEDBACK:
{review_json}

## Current paper (all sections)
{paper_text}

## Instructions
Rewrite the affected sections to address the reviewer's weaknesses, ranked by
severity (CRITICAL first, then MAJOR; MINOR only if cheap). Follow these rules:
- Only make changes the reviewer asked for, plus their minimal direct
  consequences. Do NOT rewrite unrelated content.
- Never fabricate experiments, numbers, or citations. If a fix requires data
  you do not have, soften the claim instead of inventing evidence.
- Fix overclaiming by hedging honestly ("suggests", "indicates").
- Maintain terminology consistency (Banana Rule) across all sections.
- Keep the same section structure and \\\\label commands unless the reviewer
  explicitly asked to change structure.
- Keep the abstract/introduction consistent with any changed claims.

## FORMAT RULES TO ENFORCE (apply these automatically)
- IF main.tex hyperref is not \\usepackage[draft=false,hidelinks]{{hyperref}}:
  fix it.
- IF any \\cite{{...}} has >3 keys: split into multiple \\cite calls with
  contextual phrases between them.
- IF any \\includegraphics uses width=\\columnwidth (without 0.75): change to
  width=0.75\\columnwidth,keepaspectratio.
- IF any figure float is [!t] instead of [!htb]: change it.
- IF any paragraph begins with \\textbf{{Summary:}}: remove the label, keep
  the sentence as normal body text.
- IF any section has a figure but no multi-paragraph discussion after it:
  write a 4-part discussion (background → data → mechanism → conclusion).
- ENSURE exactly 30 unique citation keys are cited, and references.bib
  contains exactly those 30 entries (remove uncited, add missing).

Return ONLY the complete revised LaTeX for ALL sections, each wrapped in:
===== BEGIN FILE: <filename> =====
<latex content>
===== END FILE: <filename> =====

Use exactly the filenames provided in the current paper. Do not drop or merge
sections. If a section is unchanged, still include it in full.
"""


# ---------------------------------------------------------------------------
# Phase 4: Final audit + report
# ---------------------------------------------------------------------------

FINAL_AUDIT = """Run the final scientific writing quality audit on the paper below.
In addition to the 5 writing passes, also check the 8 format rules.

## Paper (all sections)
{paper_text}

## Five audit passes
Pass 1 (Clutter Extraction): strip sentences to cleanest components. Flag
  "due to the fact that", "in order to", "a number of", "It is worth noting
  that", AI-isms (delve, pivotal, landscape, tapestry, underscore, noteworthy,
  intriguingly), redundancies ("completely eliminate").
Pass 2 (Active Voice): flag passive constructions where an actor exists
  ("was observed", "were analyzed"). Passive is OK for established facts.
Pass 3 (Sentence Architecture): flag sentences > 40 words, subject far from
  verb, consecutive sentences starting with "This" or "We", paragraphs doing
  two jobs.
Pass 4 (Keyword Consistency, Banana Rule): flag synonym substitution for a
  defined technical term across sections. Verify every acronym is defined at
  first use.
Pass 5 (Numerical and Citation Integrity): check N/percentages consistency
  between abstract and tables, significant figures, stats cited only via
  secondary sources.

## Eight format rules
Pass 6 (Hyperref): main.tex must use \\usepackage[draft=false,hidelinks]{{hyperref}}.
Pass 7 (Abstract Structure): must follow 7-sentence flow.
Pass 8 (Figure Discussions): every figure needs 4-part discussion after it.
Pass 9 (No Summary Label): no \\textbf{{Summary:}} anywhere.
Pass 10 (Figure Size): width=0.75\\columnwidth, float=[!htb].
Pass 11 (Citation Density): every \\cite has ≤3 keys.
Pass 12 (Reference Count): exactly 30 unique citation keys.
Pass 13 (Orphan Bib): every bib entry cited, every cite key in bib.

Return ONLY a JSON object:
{{
  "issues": [
    {{"pass": 1, "severity": "MAJOR|MINOR", "issue": "...", "location": "...", "suggestion": "..."}}
  ],
  "passes_clean": [true, true, true, true, true, true, true, true, true, true, true, true, true],
  "overall": "clean|minor_issues|needs_revision"
}}
"""

FINAL_REPORT_TEMPLATE = """# Paper Writing Agent Report

**Input**: {input_desc}
**Venue**: {venue}
**Date**: {date}
**Model**: {model}

## Pipeline Summary

| Phase | Status | Output |
|-------|--------|--------|
| 1. Planning | {plan_status} | PAPER_PLAN.md |
| 2. Writing | {write_status} | paper/sections/*.tex |
| 3. Review Loop | {review_status} | {rounds} round(s), score {score}/10 |
| 4. Finalize | {finalize_status} | paper/main.tex |

## Review Scores

| Round | Score | Verdict | Key Changes |
|-------|-------|---------|-------------|
{score_table}

## Final Audit
- Passes clean: {passes_clean}
- Overall: {audit_overall}

## Deliverables
- paper/PAPER_PLAN.md — outline with claims-evidence matrix
- paper/sections/*.tex — section LaTeX
- paper/references.bib — bibliography (only cited entries)
- paper/main.tex — master file

## Remaining Issues
{remaining_issues}

## Next Steps
- [ ] Visual inspection of compiled PDF
- [ ] Verify any [VERIFY] citations manually via DBLP/CrossRef
- [ ] Add any missing manual figures
- [ ] Submit to {venue}
"""

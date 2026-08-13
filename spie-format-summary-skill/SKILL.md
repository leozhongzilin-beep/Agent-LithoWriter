---
name: spie-format-summary
description: Create a SPIE-journal-format summary and handoff contract from rough research material, drafts, literature notes, experiment summaries, or another agent's output. Use when an agent needs to summarize content for SPIE manuscript generation, prepare a compact SPIE writing brief, convert loose paper material into a SPIE-compatible input contract, or hand off material to a SPIE formatter/paper-writing agent.
---

# SPIE Format Summary

Use this skill as a lightweight bridge before calling a full SPIE paper-writing or formatting agent.

It produces two handoff artifacts:

- `SPIE_FORMAT_SUMMARY.md`: compact human/agent-readable summary of the paper material.
- `SPIE_INPUT_CONTRACT.md`: Markdown contract compatible with the SPIE paper agent.

## Workflow

1. Read the user's source material: topic, notes, rough draft, `.md`, `.txt`, `.docx`, literature matrix, reviewer notes, result summaries, or another agent's output.
2. If a file is supplied, prefer the bundled script:

```powershell
python skills/spie-format-summary/scripts/summarize_to_spie_contract.py source.md --out-dir output-dir
```

3. If no file is supplied, write the same two artifacts manually from the conversation.
4. Preserve scientific boundaries:
   - Do not invent experimental results.
   - Do not invent author names, affiliations, emails, funding, DOI, or references.
   - Mark missing material with `TODO:` or `DATA_NEEDED:`.
   - Keep claims scoped to the evidence present in the source material.
5. Output the summary first, then the input contract.
6. Hand `SPIE_INPUT_CONTRACT.md` to `spie-paper-agent` or another paper-writing agent.

## Summary Structure

`SPIE_FORMAT_SUMMARY.md` should contain:

```markdown
# SPIE_FORMAT_SUMMARY

## Target
- Journal/format: SPIE journal manuscript
- Output expected: DOCX or SPIE input contract

## Paper Identity
- Proposed title:
- Research topic:
- Authors:
- Affiliations:
- Corresponding author:

## Research Story
- Background:
- Problem/gap:
- Method:
- Data/experiment:
- Main result:
- Contribution:
- Limitations:

## Evidence Inventory
- References:
- Figures:
- Tables:
- Equations:
- Raw data:
- Missing data:

## SPIE Format Readiness
- Abstract:
- Keywords:
- Numbered sections:
- Disclosures:
- Data availability:
- Acknowledgments:
- References:

## Handoff Notes
- Safe claims:
- Claims needing evidence:
- Data needed from user:
```

## Input Contract Structure

`SPIE_INPUT_CONTRACT.md` must follow this shape:

```markdown
# Title
...

# Authors
...

# Affiliations
...

# Corresponding Author
...

# Abstract
...

# Keywords
...

# Sections
## 1 Introduction
...

## 2 Materials and Methods
...

## 3 Results
...

## 4 Discussion
...

## 5 Conclusion
...

# Disclosures
...

# Code, Data, and Materials Availability
...

# Acknowledgments
...

# References
1. ...
```

## Writing Rules

- Abstract: use a five-part flow: what, why hard, how, evidence, strongest result or evidence boundary.
- Introduction: include background, gap, approach overview, contribution, result preview, and roadmap.
- Method: define notation, variables, equations, algorithm/workflow, and experimental setup when available.
- Results: only state results supported by supplied data; otherwise use `DATA_NEEDED:`.
- Discussion: explain implications and limitations without adding new unsupported claims.
- References: keep numbered references; use `TODO: Add verified references.` when not supplied.

## Handoff Rule

End by telling the caller which file to pass forward:

```text
Next agent input: SPIE_INPUT_CONTRACT.md
```


# Literature Knowledge Base & RAG Specification v1.0

## 0. 文档信息

- **名称**：Literature Knowledge Base & RAG Specification
- **版本**：v1.0
- **目标领域**：Computational Lithography / ILT / OPC / SMO / AI-ILT / Lithography Simulation / Mask Optimization
- **服务对象**：Research Writing Agent、Literature Review Agent、Experiment Planning Agent、Citation/Reference Manager
- **核心目标**：以最小上下文成本，为科研 Agent 提供可追溯、可分级、可验证的论文知识、公式、实验数据与引用信息。

---

# 1. 设计目标

## 1.1 核心目标

系统不是一个“论文全文向量数据库”，而是一个：

> **Hierarchical Literature Knowledge System + Evidence Retrieval System + Citation Management System**

核心原则：

1. **Progressive Retrieval**：先粗后细，按需升级检索层级。
2. **Minimum Context**：默认只返回完成当前任务所需的最少上下文。
3. **Structured over Raw**：优先结构化知识，全文只作为最终证据源。
4. **Evidence First**：所有重要 Claim、Metric、Formula 均尽可能回溯到原始证据。
5. **Source Traceability**：任何知识对象都必须能定位到 Paper / Section / Page / Paragraph / Figure / Table。
6. **Citation Separation**：文献元数据、Citation Key、最终 Citation Rendering 分离。
7. **Multi-Modal Retrieval**：结合 metadata、keyword、BM25、vector、graph 检索。
8. **No Fabrication**：原文未报告的数据不得推测或补全为事实。
9. **Comparability Awareness**：不同实验条件下的指标不得被默认横向比较。
10. **Agent-Driven**：Agent 根据任务决定需要检索到哪一级。

---

# 2. 总体架构

```text
                         Writing Agent
                              |
                     Retrieval Router
                              |
        +---------------------+----------------------+
        |                     |                      |
   Literature RAG        Formula RAG          Citation Manager
        |                     |                      |
        +----------+----------+----------+-----------+
                   |                     |
                   v                     v
            Hierarchical KB       Citation Graph
                   |
      +------------+-------------+
      |            |             |
      v            v             v
     L0           L1            L2
 Discovery    Understanding   Structured Facts
      |            |             |
      +------------+-------------+
                   v
                  L3
               Evidence
                   |
                   v
                  L4
              Full Text
```

---

# 3. 分层知识架构

## 3.1 L0 — Paper Index / Discovery Layer

### 目标

最低 token 成本地回答：

- 知识库有哪些相关论文？
- 某篇论文大概讲什么？
- 论文基本元数据是什么？
- 如何引用它？
- 是否值得进一步深入？

### L0 必备字段

```yaml
paper_id:
title:
one_line_description:
authors_summary:
year:
venue:
article_type:
doi:
url:
keywords:
domain_tags:
method_tags:

bibliographic_record:
  authors:
  title:
  container_title:
  year:
  volume:
  issue:
  pages:
  article_number:
  publisher:
  doi:
  url:

citation_key:

citation_cache:
  bibtex:
  ieee:
  nature:
  custom:

pointers:
  l1:
  l2:
  l3:
  l4:
  formula:
```

### 规则

- `bibliographic_record` 是唯一的文献元数据真相源。
- `citation_cache` 是可重建的缓存，不是唯一真相。
- `one_line_description` 用于相关性判断，不承担证据功能。
- L0 默认控制在约 50–150 tokens / paper。

---

# 4. L1 — Paper Understanding Layer

## 4.1 目标

让 Agent 不读全文即可理解论文的研究内容、贡献、局限及适用写作位置。

## 4.2 字段

```yaml
abstract:
research_problem:
research_gap:
main_idea:
method_summary:
main_contributions:
innovation:
key_findings_summary:
limitations:
datasets_summary:
methods_summary:
recommended_use:
  background:
  motivation:
  related_work:
  method:
  discussion:
  citation_evidence:
```

### 推荐长度

150–500 tokens / paper。

### 关键原则

L1 是“理解层”，不是原文证据层。所有重要结论应存在对应 L3 Evidence。

---

# 5. L2 — Structured Facts / Method & Result Layer

L2 是本系统面向计算光刻研究最重要的结构化层。

## 5.1 L2-A Method Card

```yaml
method:
  method_name:
  method_family:
  task:
  input:
  output:
  architecture:
  algorithm:
  optimization:
  loss_function:
  training_strategy:
  inference_strategy:
  iterative_or_direct:

system_context:
  technology_node:
  wavelength:
  numerical_aperture:
  resist_model:
  lithography_condition:
  pattern_type:
  resolution:
```

## 5.2 L2-B Result Card

必须优先覆盖计算光刻常用指标：

```yaml
metrics:
  EPE:
  CD:
  PVBand:
  TAT:
  shots:
  runtime:
  inference_time:
  optimization_time:
  memory:
  throughput:
  accuracy:
  loss:
```

但不限制为以上指标；不同论文可扩展。

## 5.3 Metric Object 标准结构

禁止只存：

```text
EPE = 2.3 nm
```

必须至少保存：

```yaml
metric_id:
name:
value:
unit:
status: reported | not_reported | not_applicable | unclear
condition:
  dataset:
  pattern:
  pitch:
  wavelength:
  NA:
  technology:
  dose:
  focus:
  resolution:
  hardware:
baseline:
source_evidence_id:
source_page:
source_section:
```

## 5.4 Comparison Object

```yaml
comparison_id:
metric:
condition:
baseline:
proposed:
improvement:
comparison_validity: comparable | partially_comparable | not_comparable
source_evidence_id:
```

绝不能默认用不同数据集、不同硬件、不同 lithography condition 下的指标做排名。

---

# 6. L3 — Evidence Layer

L3 是科研写作最重要的“证据层”。

## 6.1 Evidence Object

```yaml
evidence_id:
paper_id:
section:
subsection:
page:
paragraph_index:
figure_ref:
table_ref:
source_text:
claim:
evidence_type:
  definition
  methodological_statement
  observation
  experimental_result
  comparison
  limitation
  causal_claim
  quantitative_result

supports_claim_ids:
formula_refs:
metric_refs:
confidence:
```

## 6.2 设计原则

- L3 可以比全文 chunk 更短。
- 主要检索单元推荐为 paragraph / evidence block，而不是单 sentence。
- 每条重要证据必须保留原文文本。
- 必须有 page / section 等定位信息，条件允许时同时保存 figure/table。
- Agent 需要引用证据时，默认先检索 L3，而不是 L4。

---

# 7. L4 — Full Text Layer

L4 保存经过解析的全文，并保留原始 PDF / XML / HTML / LaTeX 等 source pointer。

结构：

```text
Paper
├── Abstract
├── Introduction
├── Related Work
├── Method
│   ├── Section
│   └── Subsection
├── Experiment
├── Discussion
└── Conclusion
```

每个 section 下保存 paragraph-level chunks。

### 规则

- L4 是原始事实源，不是默认 RAG 上下文。
- 默认不向 Writing Agent 返回全文。
- 只有当 L3 不足以回答问题时才进入 L4。

---

# 8. Formula Knowledge Base

公式库独立于论文全文，但每个公式必须能回溯到来源。

## 8.1 Formula Object

```yaml
formula_id:
paper_id:
section:
page:
formula_latex:
formula_image_ref:
formula_role:
  objective
  loss
  forward_model
  constraint
  regularization
  metric
  physical_model
  evaluation
  network
  update_rule

semantic_description:
variables:
  - symbol:
    meaning:
    unit:
application:
assumptions:
related_formulas:
reusability:
  directly_reusable: true | false
  requires_context: true | false
notation_dependencies:
source_evidence_id:
```

## 8.2 公式检索

公式 embedding 不得只使用 LaTeX，应组合：

- LaTeX
- semantic description
- formula role
- variable meanings
- application/task

---

# 9. Concept / Method Ontology

用于扩展用户查询和统一术语。

例如：

```text
AI-ILT
├── Deep-learning ILT
├── CNN-based ILT
├── Transformer-based ILT
├── Neural mask synthesis
└── Learning-based mask optimization
```

Concept Object：

```yaml
concept_id:
canonical_name:
aliases:
parent_concepts:
child_concepts:
related_concepts:
related_methods:
related_papers:
```

---

# 10. Metric Ontology

统一 EPE、CD、PV Band、TAT、Shots 等指标。

每个 Metric 应定义：

```yaml
metric_name:
canonical_definition:
aliases:
unit:
category:
measurement_scope:
comparability_rules:
common_pitfalls:
```

例如 TAT 不得自动等价于 inference time。

---

# 11. Citation Architecture

必须分成三个概念：

## 11.1 Bibliographic Record

描述“这是什么论文”。

## 11.2 Citation Key

例如：

```text
Zhang2024Transformer
```

Writing Agent 内部使用 citation key，而不直接写 [12]。

## 11.3 Citation Rendering

根据目标期刊动态渲染最终格式。

推荐采用 CSL/Citation Style Language 体系，而不是在代码中硬编码几十种期刊格式。

### 结构

```text
Canonical Bibliographic Record
            |
            +----> CSL Style: Nature
            +----> CSL Style: IEEE
            +----> CSL Style: OLT
            +----> CSL Style: Custom
```

### Citation Object

```yaml
paper_id:
citation_key:
style_id:
in_text_citation:
bibliography_entry:
reference_number:
style_source:
style_version:
```

### 原则

- L0 可以缓存最常用格式，方便低 token 直接取用。
- 但最终格式始终可以从 canonical metadata 重新生成。
- 引用编号由 Citation Manager 管理，不由 LLM 手工维护。

---

# 12. Citation Graph

不仅保存 A cites B，还建议支持：

```text
cites
extends
improves
compares_with
uses
criticizes
builds_on
same_method_family
```

结构：

```yaml
source_paper:
target_paper:
relation:
evidence_id:
confidence:
```

---

# 13. Retrieval Architecture

## 13.1 Retrieval Router

Agent 不直接决定“搜全文”，而发送意图。

推荐模式：

```text
DISCOVERY
CITATION
TECHNICAL
FORMULA
RESULT
VERIFICATION
COMPARISON
```

## 13.2 Progressive Retrieval

```text
Query
 ↓
L0
 ↓ sufficient? → return
 ↓ insufficient
L1
 ↓ sufficient? → return
 ↓ insufficient
L2
 ↓ evidence needed?
L3
 ↓ still insufficient?
L4
```

## 13.3 默认 token budget

```yaml
L0: 50-150 tokens / paper
L1: 150-500 tokens / paper
L2: 300-1000 tokens / relevant paper or query
L3: 300-1500 tokens / evidence set
L4: on-demand only
```

---

# 14. Hybrid Retrieval

检索层应组合：

1. Metadata filtering
2. Exact keyword / BM25
3. Vector semantic retrieval
4. Reranking
5. Citation graph retrieval
6. Concept expansion

例如：

```text
year >= 2020
AND domain = ILT
AND method = Transformer
```

先做 metadata filter，再做 semantic retrieval。

---

# 15. Retrieval Return Contract

禁止直接返回大片文本。

推荐统一返回：

```yaml
query:
mode:
results:
  - paper_id:
    title:
    relevance:
    why_relevant:
    best_use:
    key_fact:
    citation_key:
    citation:
    available_levels:
    evidence_ids:
next_action:
```

---

# 16. Evidence & Hallucination Control

## 16.1 所有关键 Claim 必须可追溯

```text
Claim
 ↓
Evidence
 ↓
Paper
 ↓
Section
 ↓
Page
```

## 16.2 数据状态

对所有数字字段区分：

- reported
- not_reported
- not_applicable
- unclear

不能使用“合理推测”的数据填入 reported。

## 16.3 Claim Strength

建议：

```text
A = Direct evidence
B = Strong support
C = Indirect support
D = Background only
```

Writing Agent 不得用 C/D 级证据写成强因果结论。

---

# 17. Paper Processing Pipeline

```text
PDF / XML / HTML / LaTeX
        ↓
Document Parsing
        ↓
Metadata Extraction
        ↓
Bibliographic Reconciliation
        ↓
Section / Paragraph Parsing
        ↓
Concept / Method Extraction
        ↓
Metric Extraction
        ↓
Formula Extraction
        ↓
Claim / Evidence Extraction
        ↓
Citation Graph Extraction
        ↓
Validation
        ↓
L0-L4 + Formula + Graph Records
```

---

# 18. 元数据验证优先级

推荐：

```text
Publisher / DOI metadata
    > DOI resolver metadata
    > Structured XML
    > PDF metadata
    > OCR / extracted text
    > LLM inference
```

LLM 只能作为补全或解释工具，不得覆盖高置信元数据。

---

# 19. Quality Gates

论文进入正式数据库前必须通过：

### QG-1 Metadata

- Title matches source
- Authors validated
- DOI validated when available
- Venue validated

### QG-2 L2 Numeric Facts

- Every reported metric has source_evidence_id
- Every metric has unit when applicable
- Experimental condition is preserved

### QG-3 Formula

- Formula linked to original paper
- Variables identified
- Formula role classified

### QG-4 Citation

- citation_key unique
- bibliography renderable
- unresolved metadata flagged

### QG-5 Evidence

- claim linked to source evidence
- page/section available when possible

---

# 20. Recommended Database Collections / Tables

```text
papers
paper_cards
paper_methods
paper_metrics
paper_comparisons
paper_claims
paper_evidence
paper_fulltext
formulas
formula_variables
concepts
metrics_ontology
citation_records
citation_styles
citation_graph
embeddings
processing_jobs
validation_reports
```

关系数据库负责 metadata / relations；vector index 负责 semantic retrieval；search index 负责 lexical search；graph store 可选。

---

# 21. Agent API 建议

## search_papers

```json
{
  "query": "transformer based inverse lithography",
  "mode": "discovery",
  "filters": {
    "year_from": 2020,
    "domain": "ILT"
  },
  "max_tokens": 1000
}
```

## get_paper_card

```json
{
  "paper_id": "ILT_2024_031",
  "level": "L1"
}
```

## get_structured_results

```json
{
  "paper_id": "ILT_2024_031",
  "metrics": ["EPE", "TAT", "PVBand"]
}
```

## search_evidence

```json
{
  "claim": "deep learning reduces iterative ILT computation",
  "paper_ids": [],
  "max_tokens": 1200
}
```

## search_formulas

```json
{
  "query": "ILT objective function with regularization",
  "formula_role": "objective",
  "max_tokens": 800
}
```

## resolve_citation

```json
{
  "paper_id": "ILT_2024_031",
  "style_id": "optics_and_laser_technology"
}
```

## verify_claim

```json
{
  "claim": "The proposed method reduces TAT by 90%",
  "candidate_papers": ["ILT_2024_031"]
}
```

---

# 22. Writing Agent Integration Policy

Writing Agent 在生成论文时应遵循：

1. 先判断是否需要 citation。
2. Citation search 默认从 L0 开始。
3. 需要理解研究内容时升级到 L1。
4. 需要具体数字时优先 L2。
5. 需要证明某句话时升级到 L3。
6. 只有 L3 不足时读取 L4。
7. 公式优先从 Formula KB 查找。
8. 正文内部使用 citation key。
9. 最终参考文献由 Citation Manager 按目标期刊样式渲染。
10. 所有 quantitative claims 必须有 evidence trace。

---

# 23. 推荐的最终 Paper Object

```yaml
paper_id:
title:
one_line_description:
authors:
year:
venue:
doi:
url:

bibliographic_record: {}
citation_key:

L1:
  abstract:
  research_problem:
  research_gap:
  main_idea:
  contributions: []
  limitations: []
  recommended_use: {}

L2:
  method_card: {}
  result_card: {}
  metrics: []
  comparisons: []

L3:
  evidence: []
  claims: []

L4:
  fulltext_pointer:
  section_index: []

formula_refs: []
citation_graph_refs: []
concept_refs: []
validation_report: {}
```

---

# 24. 最终设计原则

本系统的核心并非“储存最多论文”，而是：

> **用最少 token 找到最合适的论文、最合适的事实、最合适的公式、最合适的证据，并保证最终引用可以追溯和正确渲染。**

理想工作流：

```text
Writing Intent
   ↓
L0 Discovery
   ↓
L1 Understanding
   ↓
L2 Structured Facts
   ↓
L3 Evidence
   ↓
Formula / Citation Manager
   ↓
Final Manuscript
```

**End of Specification v1.0**

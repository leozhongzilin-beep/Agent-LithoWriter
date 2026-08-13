# Skill: Paper-to-Literature-KB v1.0

## 0. Skill Name

`paper_to_literature_kb`

## 1. Purpose

将一篇论文原文（PDF / XML / HTML / LaTeX）处理为可直接进入 Literature Knowledge Base 的结构化对象。

输出必须包含：

- L0 Paper Index
- L1 Paper Understanding
- L2 Method & Result Cards
- L3 Evidence
- L4 Full-text pointer
- Formula Library objects
- Citation metadata
- Citation graph relations（当可可靠提取时）
- Validation report

核心要求：

> **宁可保留 unknown / not_reported，也不得凭常识补全论文没有报告的数据。**

---

# 2. Input

可接受：

- PDF
- Publisher HTML
- XML
- LaTeX source
- arXiv source
- DOI + 可访问全文

如果同时存在 PDF 和结构化 XML / HTML：优先使用结构化版本解析正文，PDF 作为版面/页码校验源。

---

# 3. Processing Pipeline

```text
Input Paper
   ↓
1. Parse document
   ↓
2. Extract bibliographic metadata
   ↓
3. Reconcile metadata
   ↓
4. Identify sections
   ↓
5. Extract L0
   ↓
6. Build L1
   ↓
7. Extract L2 Method Card
   ↓
8. Extract L2 Result / Metrics
   ↓
9. Extract Claims
   ↓
10. Extract Evidence
   ↓
11. Extract formulas
   ↓
12. Extract citations / citation graph
   ↓
13. Validate all outputs
   ↓
14. Emit KB package
```

---

# 4. Step 1 — Bibliographic Metadata

优先级：

```text
Publisher / DOI metadata
>
Crossref/OpenAlex/Semantic Scholar-like structured metadata
>
Structured full text
>
PDF metadata
>
LLM inference
```

提取：

```yaml
paper_id:
title:
authors:
year:
venue:
article_type:
volume:
issue:
pages:
article_number:
doi:
url:
publisher:
```

规则：

- 不确定字段设为 `unknown`。
- 不得凭搜索结果猜 DOI。
- DOI 存在时应进行 canonical normalization。
- 作者姓名尽量保存 family/given 分离结构。

---

# 5. Step 2 — Build L0

输出：

```yaml
paper_id:
title:
one_line_description:
authors_summary:
year:
venue:
keywords:
domain_tags:
method_tags:
bibliographic_record:
citation_key:
```

## 5.1 One-line description

要求：

- 1–2 句话
- 只描述论文确实做了什么
- 不夸大 contribution
- 不使用没有证据的评价词，例如 “revolutionary”, “state-of-the-art”

## 5.2 Citation

不要把 PDF 中已有的引用字符串当作唯一真相。

建立 canonical bibliographic record，并允许后续 citation renderer 根据目标期刊生成格式。

---

# 6. Step 3 — Build L1

从 abstract + introduction + conclusion + method overview 综合生成：

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
recommended_use:
```

## 6.1 推荐 use 标签

分别判断：

```yaml
background: none | weak | moderate | strong
motivation: none | weak | moderate | strong
related_work: none | weak | moderate | strong
method: none | weak | moderate | strong
discussion: none | weak | moderate | strong
citation_evidence: none | weak | moderate | strong
```

不要因为论文是相关领域论文就自动标记为 strong。

---

# 7. Step 4 — Build L2 Method Card

专门寻找：

- algorithm / architecture
- input/output
- optimization
- loss
- training strategy
- inference strategy
- iterative/direct
- simulation flow
- lithography model
- data generation
- datasets
- hardware

输出：

```yaml
method_card:
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
```

---

# 8. Step 5 — Build L2 Result Card

## 8.1 优先检索的计算光刻指标

必须主动搜索：

```text
EPE
CD
PV Band / PVBand
TAT / turnaround time
shots
runtime
inference time
optimization time
memory
throughput
mask complexity
accuracy
loss
```

也要识别论文自定义指标。

## 8.2 每个数值必须绑定条件

错误：

```text
EPE = 2.1 nm
```

正确：

```yaml
name: EPE
value: 2.1
unit: nm
status: reported
condition:
  dataset: ...
  pattern: ...
  pitch: ...
  wavelength: ...
  NA: ...
  technology: ...
  hardware: ...
source_evidence_id: ...
```

## 8.3 不要只提取“最好结果”

如果论文包含 baseline / prior work / ablation / proposed：

全部保留。

---

# 9. Step 6 — Metric Extraction Rules

对于每个 Metric：

1. 识别 metric name。
2. 识别 value。
3. 识别 unit。
4. 识别测试条件。
5. 识别 baseline。
6. 识别是否为平均值、最优值、最大值或某 case。
7. 绑定 evidence。
8. 判断是否可以与其它论文比较。

禁止：

- 从图像估读数值后冒充精确报告值。
- 根据模型复杂度推算 runtime。
- 根据论文结论推算未报告 EPE。
- 用摘要中的“significant improvement”生成具体百分比。

如果只有图中信息而无法可靠读取：

```yaml
status: unclear
```

---

# 10. Step 7 — Claims

识别论文的重要 Claim：

```yaml
claim_id:
paper_id:
claim:
claim_type:
strength:
supporting_evidence_ids:
```

claim_type 可为：

- definition
- methodological
- quantitative
- comparative
- causal
- limitation
- conclusion

---

# 11. Step 8 — Evidence Extraction

建立 L3 Evidence Object：

```yaml
evidence_id:
paper_id:
section:
subsection:
page:
figure_ref:
table_ref:
source_text:
claim:
evidence_type:
metric_refs:
formula_refs:
```

## 11.1 证据选择原则

优先保存：

- 能直接支持 Claim 的句子或 paragraph
- 定量结果所在 paragraph
- Table / Figure 对应解释文字
- Method 定义
- Limitations

尽量不要保存无关背景句作为 evidence。

---

# 12. Step 9 — Formula Extraction

主动扫描：

- equation environment
- numbered equations
- display math
- inline formulas 中具有独立数学意义的表达
- loss/objective definitions
- physical models
- metrics
- update rules

每条公式输出：

```yaml
formula_id:
paper_id:
section:
page:
formula_latex:
formula_role:
semantic_description:
variables:
application:
source_evidence_id:
reusability:
```

## 12.1 变量提取

例如：

```yaml
variables:
  - symbol: M
    meaning: mask
  - symbol: T
    meaning: target pattern
```

若变量含义无法确认，标记 `unclear`，不要猜。

---

# 13. Step 10 — Citation Metadata

如果原文没有规范 Citation String：

1. 从论文元数据构造 canonical record。
2. 生成 unique citation_key。
3. 不需要在 Paper object 中硬编码某一个期刊风格。
4. Citation renderer 后续根据 style_id 生成最终 bibliography entry。

推荐 citation_key：

```text
FirstAuthorYearShortTitle
```

例如：

```text
Zhang2024TransformerILT
```

若冲突：

```text
Zhang2024TransformerILT_a
Zhang2024TransformerILT_b
```

---

# 14. Step 11 — Citation Graph

如果可以可靠识别引用关系，提取：

```yaml
source_paper:
target_paper:
relation: cites | extends | improves | compares_with | builds_on | uses
confidence:
source_evidence_id:
```

注意：

仅仅因为 A 在 bibliography 中引用 B，不要自动推断 `improves`、`extends` 等强关系；普通 reference 默认只标记 `cites`。

---

# 15. Step 12 — Validation

必须执行以下检查。

## Metadata Validation

- Title matches source.
- Author list not truncated accidentally.
- Year valid.
- DOI syntax valid when present.
- Venue consistent.

## L2 Validation

- Every reported numeric value has unit where applicable.
- Every metric has source evidence.
- Experimental conditions are preserved where available.
- Not-reported values are not fabricated.

## Formula Validation

- Formula text matches source.
- Variable definitions traceable.
- Formula role plausible and source-supported.

## Evidence Validation

- Evidence text is actually from source.
- Page / section information is valid when available.
- Claim is supported by evidence.

## Citation Validation

- citation_key unique.
- Bibliographic record sufficient for rendering.
- Missing fields explicitly flagged.

---

# 16. Confidence Model

建议所有抽取对象带 confidence：

```text
0.95–1.00  Directly extracted / validated
0.85–0.95  Strongly supported
0.70–0.85  Extracted with ambiguity
<0.70       Requires review
```

Confidence 不是事实真值；它用于 routing 和 human review。

---

# 17. Output Contract

最终输出一个 package：

```yaml
paper:
  L0: {}
  L1: {}
  L2:
    method_card: {}
    result_card: {}
    metrics: []
    comparisons: []
  L3:
    claims: []
    evidence: []
  L4:
    fulltext_pointer:

formulas: []
citation_records: []
citation_graph: []
validation_report: {}
```

---

# 18. Human Review Triggers

以下情况必须进入人工审查队列：

1. DOI / authors / year 冲突。
2. 数值来自模糊图表且无法可靠 OCR。
3. 指标定义不明确。
4. 实验条件缺失但数字可能被误用于跨论文比较。
5. 公式无法可靠恢复。
6. Claim 与 evidence 关系不明确。
7. Citation metadata 不足以稳定生成 bibliography。

---

# 19. Agent 行为约束

你不是论文总结 Agent，而是“知识库构建 Agent”。

因此：

- 不追求文学化总结。
- 不追求覆盖所有段落。
- 优先结构化、可查询、可追溯的信息。
- 不得捏造未报告的指标。
- 不得把论文作者的宣传性结论自动变成事实。
- 不得删除对比较至关重要的实验条件。
- 不得把不同指标的术语擅自合并。
- 不得把“related”自动变成“improves”。

---

# 20. Final Internal Checklist

```text
[ ] L0 完成
[ ] Canonical bibliographic record 完成
[ ] Citation key 唯一
[ ] L1 完成
[ ] L2 Method Card 完成
[ ] L2 Result Card 完成
[ ] EPE/CD/PVBand/TAT/Shots 等已主动检查
[ ] 每个数字都有 source evidence
[ ] 实验条件已保留
[ ] Claims 已提取
[ ] Evidence 已提取
[ ] Formula 已提取
[ ] Formula variables 已解释
[ ] Citation graph 已提取（可用时）
[ ] L4 pointer 建立
[ ] Validation 完成
[ ] 人工复核项已标记
```

**End of Skill v1.0**

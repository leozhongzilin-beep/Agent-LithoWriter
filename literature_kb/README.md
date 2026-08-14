# Literature Knowledge Base — 更新日志

> 本包的功能说明已合入主 [`README.md`](../README.md)；此文件只记录 KB 的变更历史。

## 2026-08-14 — 引用解析加固 + 数据回填

- **模糊标题匹配**：新增 `KBStore.title_index()`（全库 `(paper_id, title)` 索引）；
  `RetrievalService.resolve_hint()` 在精确阶梯 miss 后追加"前缀名 recall + OR-join
  recall"，使 LLM 改写过的标题（如 "GAN-OPC: Generative ..." vs 存储的
  "GAN-OPC: Mask Optimization ..."）也能命中，不再落入 UNVERIFIED。
- **引用元数据回填**：新增 `KBStore.update_citation_metadata()` 与
  `scripts/backfill_bibtex.py`（never-guess + 标题一致性守卫），修复导入时
  doi/bibtex 被剥掉的论文。
- **Neural-ILT 数据回填**：`ILT_2022_011`（Neural-ILT，ICCAD 2020）补回
  `doi=10.1145/3400302.3415704` 与 BibTeX。
- **数据库纳入版本控制**：`.gitignore` 解除对 `kb.db` 的忽略（`raw/`、`vectors/` 仍忽略）。

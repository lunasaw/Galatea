---
name: mlflow-analysis
description: Analyze MCP-provided MLflow metric and artifact summaries for a Galatea Campaign, form compatible cohorts, and select by validation evidence without accessing tracking storage directly.
---

# MLflow Analysis

Read [references/analysis.md](references/analysis.md). Use `galatea_query_runs`, `galatea_get_metric_history`, `galatea_get_artifact`, and `galatea_compare_runs` as defined by [the frozen contract](../../contracts/tools.json). Never query the MLflow database or MinIO filesystem, and do not require their credentials.

Partition incompatible runs rather than ranking them together. Use the registered objective direction on validation metrics. Explain incomplete, noisy, underfit, overfit, or non-comparable evidence and avoid claiming global optimality. Ray success alone does not establish artifact integrity, quality-gate success, or acceptance.

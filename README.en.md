<div align="center">

# 🔬 sync-project-facts

### Research Project Fact Synchronizer

**Turn factual claims scattered across project materials into a traceable, reviewable, and maintainable fact ledger.**

[Chinese version](README.md) · English

[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-sync--project--facts-4F46E5)](SKILL.md)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](scripts)
[![Tests](https://img.shields.io/badge/tests-30%20passed-16A34A)](tests)
[![Local First](https://img.shields.io/badge/core-local--first-0F766E)](#privacy-and-safety-boundaries)
[![License](https://img.shields.io/badge/license-MIT-F59E0B)](LICENSE)

[Overview](#what-problem-it-solves) · [Capabilities](#core-capabilities) · [Get started](#get-started) · [Local memory](#optional-local-preference-memory) · [Validation](#validation-status)

</div>

---

## What Problem It Solves

A single research project often appears in a resume, paper, presentation, README, experiment log, configuration file, spreadsheet, and result summary. Over time, those materials can drift apart.

| Material | Example claim |
|---|---|
| Paper | `mAP@0.5 = 93.7%` |
| Presentation | `mAP@0.5 = 94.2%` |
| README | Still uses an outdated module name |
| Resume | Claims sole ownership of all model design and training |
| Team record | Supports only data preparation, annotation, and partial writing |

Ordinary chat comparison can identify obvious wording differences. `sync-project-facts` is designed for repeatable, high-risk audits that require exact source locations, preserved decisions, and machine-checkable outputs.

> In one sentence: this is a factual-consistency auditor for research-project materials, not a polishing assistant.

## Best Used When

- Checking a resume, project summary, and presentation before applications or interviews.
- Checking manuscript text, tables, supplementary files, and experiment records before submission.
- Finding materials that still use outdated metrics, module names, versions, or conclusions.
- Auditing whether personal-contribution claims are supported by explicit team records.
- Re-running the same audit while preserving previously confirmed decisions.

For a one-off comparison of two short documents, ordinary conversational review is usually faster. Use the full pipeline when the materials are numerous, the claims are high risk, or the result must be reproducible.

## Core Capabilities

### Multi-format evidence extraction

The core pipeline supports Markdown, text, source code, JSON, YAML, CSV, DOCX, PPTX, XLSX, and PDF. It preserves the most precise locator available for each format:

- Text and source code: line number
- PDF: page number
- DOCX: heading, paragraph, or table position
- PPTX: slide and text box
- XLSX: worksheet and cell
- JSON and YAML: key path
- CSV: row number and column name

### Eight fact statuses

| Status | Meaning |
|---|---|
| `CONSISTENT` | The same fact and scope agree completely. |
| `EQUIVALENT` | Percentages, decimals, units, rounding, or explicit aliases are provably equivalent. |
| `SCOPED_DIFFERENCE` | Values differ because the dataset, modality, metric definition, threshold, version, date, or conditions differ. |
| `STALE` | A material still uses a fact explicitly superseded by a confirmed newer one. |
| `CONTRADICTED` | Incompatible values appear within the same scope. |
| `UNSUPPORTED` | A claim exists without traceable supporting evidence. |
| `MISSING` | A fact explicitly required in a named target material is absent. |
| `UNRESOLVED` | Evidence conflicts, but the available sources cannot justify a decision. |

Personal-contribution conflicts use subtype `OWNERSHIP` and severity `High` or `Critical`.

### Conservative adjudication

The skill never selects a fact merely because most files repeat it, one file is newer, or one metric looks better. Hard conflicts retain every candidate value and source until the user decides. Confirmed human decisions are not silently overwritten on later runs.

## Outputs

A complete run produces at least:

```text
run/
├── project-facts.json
└── fact-sync-report.md
```

The report includes the material inventory, normalized fact ledger, conflict matrix, high-risk issues, repair order, unresolved questions, and per-material synchronization status. See the [synthetic PV-YOLO example](examples/pv-yolo-project/output/fact-sync-report.md).

## Get Started

### Install

Clone or download the repository into the Codex skills directory:

```powershell
git clone https://github.com/Stephen-studying/sync-project-facts.git `
  "$env:USERPROFILE\.codex\skills\sync-project-facts"
```

The core workflow runs offline. Python 3.10 or later is sufficient for text, source code, JSON, CSV, and common Office Open XML files. Optional PDF or YAML packages improve extraction and degrade gracefully when unavailable.

### Invoke in Codex

```text
Use $sync-project-facts to compare the resume, paper, presentation, and experiment
results for the same project in read-only mode. Build a fact ledger with exact
source locations, list conflicts and unresolved items, and recommend a repair order.
Do not modify the source files or automatically choose the higher metric.
```

See [SKILL.md](SKILL.md) for the complete operating contract and deterministic pipeline.

## Optional Local Preference Memory

The optional memory layer is local, user-controlled, and strictly separated from project evidence.

- It can remember explicit preferences such as concise answers, tables, outcome-first structure, or English responses.
- It stores compact interaction summaries to avoid repeating rejected response patterns.
- Ambiguous inferences remain pending until the user confirms them.
- The current request always overrides remembered preferences.
- Historical answers never count as project evidence.

Memory is stored in `.local-memory/`, which is ignored by Git. Users can inspect, remove, or reset it. See [local memory rules](references/local-memory.md).

## Workflow

> **① Scan materials** → **② Locate evidence** → **③ Compare facts** → **④ Preserve decisions** → **⑤ Produce outputs**
>
> Read-only hashes · precise locators · scope-aware classification · no silent overwrite · ledger and report

## Privacy and Safety Boundaries

- Process source materials locally by default.
- Keep all source artifacts read-only.
- Generate repair recommendations without rewriting original files.
- Never invent experiment results, deployment status, open-source status, or personal contribution.
- Never treat old answers, user preferences, or file modification time as factual evidence.
- Do not use this skill for single-document polishing, translation, defense-question generation, dataset-quality checks, file triage, or technical-route diagrams.

## Validation Status

The test suite covers same-scope contradictions, decimal-percentage equivalence, scoped metric differences, stale module names, unsupported improvement claims, ownership conflicts, Chinese paths, source-hash preservation, idempotent reruns, JSON Schema validation, precise locators, offline operation, and local-memory controls.

The repository currently has **30 passing tests**.

---
name: sync-project-facts
description: Extract, normalize, and reconcile factual claims across two or more artifacts for the same project, including resumes, presentations, manuscripts, reports, READMEs, code or configuration, spreadsheets, and experiment outputs. Use when Codex needs to check or synchronize cross-file consistency in metrics, datasets, model or module names, dates, versions, project scope, deployment status, roles, or contribution claims, produce a source-grounded canonical fact register and repair plan, and optionally reuse locally confirmed response preferences and prior interaction feedback. Do not use for single-document polishing, defense-question generation, raw dataset quality checks, file triage, or inventing missing evidence.
---

# 科研项目事实同步器

## Operating contract

Work only when at least two materials or fact sources refer to the same project. Treat every source as read-only. Create outputs in a separate run directory; never rewrite a resume, manuscript, slide deck, README, configuration, result file, or fixture.

Build a confirmed fact register, not a sentence dump. Preserve exact locators, every incompatible candidate, and prior human decisions. Never select a fact by majority vote, modification time, or the better-looking metric. Never infer personal contribution from authorship, code ownership, or emphatic wording.

Stop and explain the boundary when the request contains only one document or asks only for polishing, translation, defense questions, dataset-label QA, file triage, a technical-route diagram, innovation review, or journal matching. For a combined request, finish the fact ledger first, then hand defense questioning to `$defense-beating-simulator` as a separate step.

Keep response-preference memory separate from project evidence. Never treat an old answer, remembered preference, or interaction summary as proof of a project fact. Let the current request override memory.

## Preflight

1. Confirm that two or more supported artifacts belong to one project. Keep separate projects in separate runs.
2. Recall local preferences and relevant prior interaction summaries when `.local-memory/` exists. Apply only active style directives that do not conflict with the current request.
3. Record the input root and choose an output directory outside the scanned source tree when possible.
4. Read [conflict-rules.md](references/conflict-rules.md) before adjudicating any conflict or stale claim.
5. Read [source-authority.md](references/source-authority.md) before using a source hierarchy. Treat the hierarchy as a tracing aid, not automatic truth.
6. Read [file-locators.md](references/file-locators.md) when a format extractor warns or a locator is imprecise.
7. Read [fact-taxonomy.md](references/fact-taxonomy.md) when adding or manually reviewing fact keys and scopes.

## Use local preference memory

Read [local-memory.md](references/local-memory.md) before storing, inferring, forgetting, or resetting memory. Keep all memory local and Git-ignored.

At the start of a later interaction, recall preferences and matching summaries:

```powershell
python "$Skill/scripts/manage_user_memory.py" recall --query "<current task summary>"
```

Apply returned `style_directives` proactively. Use `remembered_interactions` to avoid repeating a rejected answer pattern, but re-read current project files before making factual claims.

When the user gives explicit feedback such as “回答简洁一点”, “用表格”, “先给结论”, or “用中文”, learn it immediately:

```powershell
python "$Skill/scripts/manage_user_memory.py" learn-feedback --text "<explicit feedback>"
```

Store a compact question and answer summary after a useful interaction so a later conversation can recall it:

```powershell
python "$Skill/scripts/manage_user_memory.py" record-interaction `
  --question "<question summary>" `
  --answer-summary "<answer summary>" `
  --feedback "<optional user feedback>" `
  --project "<project name>"
```

Do not store full conversations, source-document text, credentials, or private material by default. Put ambiguous feedback into `pending_inferences` and ask for confirmation before applying it. Use `show`, `forget`, or `reset --confirm RESET` when the user asks to inspect or remove memory.

## Run the deterministic pipeline

Use Python 3.10 or later. Prefer the standard library. Optional PDF or YAML packages may improve extraction, but their absence must not block Markdown, text, code, JSON, CSV, DOCX, PPTX, or XLSX processing.

```powershell
$Skill = "<path-to-sync-project-facts>"
$InputRoot = "<same-project-materials>"
$Run = "<separate-output-directory>"

python "$Skill/scripts/scan_sources.py" $InputRoot --output "$Run/source-manifest.json"
python "$Skill/scripts/extract_evidence.py" --manifest "$Run/source-manifest.json" --output "$Run/evidence.json"
python "$Skill/scripts/compare_artifacts.py" --evidence "$Run/evidence.json" --output "$Run/comparison.json"
python "$Skill/scripts/build_fact_ledger.py" --comparison "$Run/comparison.json" --output "$Run/project-facts.json"
python "$Skill/scripts/validate_fact_ledger.py" --ledger "$Run/project-facts.json" --schema "$Skill/schemas/project-facts.schema.json" --check-sources --output "$Run/validation.json"
python "$Skill/scripts/render_sync_report.py" --ledger "$Run/project-facts.json" --output "$Run/fact-sync-report.md"
```

Use `--requirements <json>` with `compare_artifacts.py` only when the user explicitly identifies a fact that must appear in a named target material. Do not classify ordinary omission as `MISSING`.

On later runs, keep the existing ledger at the output path or pass it through `build_fact_ledger.py --existing`. Preserve any decision with `state: confirmed` or `human_confirmed: true`; surface new source disagreement instead of overwriting it. Use `validate_fact_ledger.py --baseline <prior-ledger>` when auditing decision preservation.

## Review before delivery

Inspect extractor warnings and candidate coverage. Treat heuristic extraction as a candidate generator, especially for natural-language ownership, deployment, and improvement claims. Add or correct candidate facts only from located source text or an explicit user decision.

Apply statuses exactly:

- `CONSISTENT`: same fact and scope after normalization.
- `EQUIVALENT`: provable unit, percentage, decimal, rounding, or explicit-alias equivalence.
- `SCOPED_DIFFERENCE`: values differ across explicit dataset, modality, metric definition, threshold, version, date, or conditions.
- `STALE`: an explicit current/old relationship shows that one material is superseded.
- `CONTRADICTED`: incompatible values in the same scope; do not select either.
- `UNSUPPORTED`: a claim requiring traceable proof has only claim-level evidence.
- `MISSING`: an explicit sync requirement is absent from the named target.
- `UNRESOLVED`: incompatible primary/formal evidence remains undecidable.

Set conflicting personal-contribution facts to subtype `OWNERSHIP` and severity `High` or `Critical`. Ask the user to decide when evidence cannot resolve a fact. Record the decision and its rationale; never silently replace variants.

## Deliverables

Deliver at least:

- `project-facts.json`, validated against [project-facts.schema.json](schemas/project-facts.schema.json)
- `fact-sync-report.md`, rendered from [sync-report.template.md](assets/sync-report.template.md)

Require the report to contain the material inventory, normalized ledger, conflict matrix, high-risk issues, repair order, unresolved questions, and per-material sync status. Verify that every evidence entry includes a source hash and a format-appropriate locator. Report limitations and extraction warnings without claiming that unreadable content was checked.

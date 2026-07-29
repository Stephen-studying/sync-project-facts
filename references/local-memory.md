# Local preference memory

Use the memory layer only to improve how the Skill communicates and repeats confirmed workflows. Keep it separate from `project-facts.json`, which remains the authority for project facts and adjudications.

## Storage

The default memory directory is `<skill-root>/.local-memory/`:

- `user-memory.json`: confirmed response preferences and active correction rules.
- `interaction-history.jsonl`: question, answer, and feedback summaries for later recall.

The directory is ignored by Git. Do not upload it, include source documents, store credentials, or copy full conversations by default.

## Learning policy

- Automatically save explicit style feedback such as “回答简洁一点”, “用表格”, “先给结论”, or “用中文”.
- Save an explicit `set-preference` command immediately.
- Put ambiguous or inferred preferences in `pending_inferences`; do not apply them until the user confirms them.
- Treat a newer explicit preference for the same key as a replacement, not an additional conflicting rule.
- Never turn a remembered style preference into permission to edit files, publish data, send messages, or broaden personal contribution.

## Cross-session use

At the start of a later run, call `recall` with a short query describing the current task. Apply returned `style_directives` unless the current user request overrides them. Use `remembered_interactions` only as context; re-check live project files and never treat an old answer as current evidence.

At the end of a useful interaction, call `record-interaction` with summaries rather than full source material. When the user corrects the answer, call `learn-feedback` before the next response so the correction becomes active.

## User control

Use `show` to inspect stored memory, `forget --key <preference>` to remove one preference, and `reset --confirm RESET` to remove the complete local profile and history. Report any memory change explicitly.

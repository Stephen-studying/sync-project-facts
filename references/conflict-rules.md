# Conflict rules

Apply the following order:

1. Match category and fact key.
2. Compare dataset, split, modality, model version, metric definition, thresholds, experiment date, and train/test conditions.
3. Separate explicit scope differences before comparing values.
4. Normalize decimal/percentage forms, convertible units, conservative rounding, and explicit aliases.
5. Check explicit lifecycle labels such as current, confirmed, old, or deprecated.
6. Check whether proof-required claims have primary evidence.
7. Retain all incompatible candidates and exact sources.

Never:

- decide by file count or wording frequency;
- decide from modification time alone;
- select the higher or more flattering metric;
- let a summary overwrite a raw result silently;
- infer ownership from file, repository, or code authorship;
- invent a value, deployment, publication, patent, test, or contribution state.

Use `UNRESOLVED` when incompatible formal or primary evidence remains equally plausible. Use `CONTRADICTED` for an observed same-scope incompatibility in summary/narrative materials. Both preserve candidates; neither authorizes an automatic canonical value.

Use `STALE` only when a source explicitly marks a value as current/confirmed and another as old/deprecated, or when a preserved human decision identifies the replacement. Modification time is not lifecycle evidence.

Use `MISSING` only from an explicit sync-requirements record naming the target material and fact. Silence in an ordinary artifact is not missingness.


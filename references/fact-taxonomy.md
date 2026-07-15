# Fact taxonomy

Use stable, lower-snake-case keys. Keep one semantic fact key across artifacts and express experimental differences in `scope`, not in ad hoc key suffixes.

| Category | Typical keys | Required scope checks |
|---|---|---|
| `project_identity` | `project_name`, `project_positioning` | project or product boundary |
| `timeline_version` | `project_date`, `experiment_date`, `model_version` | version and date type |
| `dataset` | `dataset`, `dataset_size`, `data_source` | dataset name, release, modality |
| `split` | `data_split` | train/validation/test rule and leakage boundary |
| `model_method` | `model_name`, `module_name`, `baseline_name` | version, modality, task |
| `metric` | `map_0_5`, `map_0_5_0_95`, `precision`, `fps` | dataset, split, modality, definition, IoU/confidence threshold, test conditions |
| `contribution` | `personal_contribution`, `team_contribution` | person, phase, task boundary |
| `outcome_status` | `paper_status`, `patent_status`, `award_status` | date and official status wording |
| `deployment` | `deployment_status`, `deployment_readiness` | distinguish actual deployment from claimed readiness; record environment |
| `openness` | `open_source_status` | repository/release target |
| `testing` | `real_test_status` | site, date, protocol, sample scope |
| `limitation` | `limitation` | model/version/experiment boundary |
| `completion_boundary` | `completion_boundary` | completed, prototype, planned, or unverified work |

Use only these standard scope fields when possible: `dataset`, `data_split`, `modality`, `model_version`, `metric_definition`, `iou_threshold`, `confidence_threshold`, `experiment_date`, `training_conditions`, and `test_conditions`. Put unavoidable domain-specific dimensions under `scope.extra`.

Do not merge claims merely because they share a number. Compare identity and scope before value normalization.

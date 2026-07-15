# Project Fact Sync Report

- Project: PV-YOLO 光伏缺陷检测
- Ledger: `ledger-08c4b90a30ce3b7d`
- Mode: read-only; no source artifact was modified

## 1. 材料清单

| Material | Type | SHA-256 | Modified | Bytes | Extraction notes |
|---|---|---|---|---|---|
| 论文事实.json | json | e5cbee2c1f78 | 2026-07-15T17:45:35Z | 1173 | — |
| 项目分工.csv | csv | d6cc7e121ae2 | 2026-07-15T17:45:41Z | 169 | Decoded as utf-8-sig. |
| 项目汇报.md | markdown | 70936decc4b1 | 2026-07-15T17:45:38Z | 291 | Decoded as utf-8-sig. |

## 2. 规范化事实总账

| Fact | Canonical / candidates | Scope | Status | Severity | Confidence | Evidence |
|---|---|---|---|---|---|---|
| contribution.personal_contribution (`fact-1f09a0560500f244`) | 个人工作:独立设计并训练全部模型。<br>负责数据整理、标注和部分论文写作<br>负责数据整理、标注和部分写作 | — | CONTRADICTED | Critical | 0.95 | `论文事实.json` @ $.facts[3]<br>`项目汇报.md` @ line 11<br>`项目分工.csv` @ row 2, column "贡献说明" |
| deployment.deployment_readiness (`fact-b85157af088c39e3`) | True | — | UNSUPPORTED | High | 0.70 | `项目汇报.md` @ line 9 |
| metric.map_0_5 (`fact-ae85323996370485`) | 93.7 %<br>94.2 % | {"dataset": "PV-Synth", "iou_threshold": "0.5", "metric_definition": "map@0.5", "modality": "RGB", "model_version": "v2.0"} | CONTRADICTED | High | 0.95 | `项目汇报.md` @ line 7<br>`论文事实.json` @ $.facts[2] |
| metric.performance_improvement (`fact-aca02130d31c96b6`) | 12 % | {"metric_definition": "performanceimprovement"} | UNSUPPORTED | High | 0.70 | `项目汇报.md` @ line 9 |
| model_method.module_name (`fact-36a5e1c891957016`) | DMMA | — | STALE | Medium | 0.85 | `论文事实.json` @ $.facts[1]<br>`项目汇报.md` @ line 5 |
| project_identity.project_name (`fact-b67a638d562dfdec`) | PV-YOLO 光伏缺陷检测 | — | CONSISTENT | Low | 0.95 | `项目汇报.md` @ line 3<br>`论文事实.json` @ $.facts[0] |
| timeline_version.model_version (`fact-53793e4f3c0538b3`) | v2.0 | {"dataset": "PV-Synth", "iou_threshold": "0.5", "modality": "RGB", "model_version": "v2.0"} | CONSISTENT | Low | 0.60 | `项目汇报.md` @ line 7 |

## 3. 冲突矩阵

| Fact | Canonical/候选事实 | 论文事实.json | Material B / others | Status | Severity | Evidence | Repair |
|---|---|---|---|---|---|---|---|
| contribution.personal_contribution (`fact-1f09a0560500f244`) | 个人工作:独立设计并训练全部模型。 / 负责数据整理、标注和部分论文写作 / 负责数据整理、标注和部分写作 | 负责数据整理、标注和部分论文写作<br><small>$.facts[3]</small> | **项目分工.csv**: 负责数据整理、标注和部分写作<br><small>row 2, column "贡献说明"</small><br>**项目汇报.md**: 个人工作:独立设计并训练全部模型。<br><small>line 11</small> | CONTRADICTED | Critical | `论文事实.json` @ $.facts[3]<br>`项目汇报.md` @ line 11<br>`项目分工.csv` @ row 2, column "贡献说明" | Stop using the broader ownership claim; obtain an explicit team/user confirmation before synchronizing contribution wording. |
| deployment.deployment_readiness (`fact-b85157af088c39e3`) | True | — | **项目分工.csv**: —<br>**项目汇报.md**: True<br><small>line 9</small> | UNSUPPORTED | High | `项目汇报.md` @ line 9 | Remove or qualify the claim until a result table, log, calculation, configuration, or explicit record is linked. |
| metric.map_0_5 (`fact-ae85323996370485`) | 93.7 % / 94.2 % | 93.7 % [{"dataset": "PV-Synth", "iou_threshold": "0.5", "metric_definition": "map@0.5", "modality": "RGB", "model_version": "v2.0"}]<br><small>$.facts[2]</small> | **项目分工.csv**: —<br>**项目汇报.md**: 94.2 % [{"dataset": "PV-Synth", "iou_threshold": "0.5", "metric_definition": "map@0.5", "modality": "RGB", "model_version": "v2.0"}]<br><small>line 7</small> | CONTRADICTED | High | `项目汇报.md` @ line 7<br>`论文事实.json` @ $.facts[2] | Do not choose a value automatically; trace the originating result/configuration and obtain confirmation. |
| metric.performance_improvement (`fact-aca02130d31c96b6`) | 12 % | — | **项目分工.csv**: —<br>**项目汇报.md**: 12 % [{"metric_definition": "performanceimprovement"}]<br><small>line 9</small> | UNSUPPORTED | High | `项目汇报.md` @ line 9 | Remove or qualify the claim until a result table, log, calculation, configuration, or explicit record is linked. |
| model_method.module_name (`fact-36a5e1c891957016`) | DMMA | DMMA<br><small>$.facts[1]</small> | **项目分工.csv**: —<br>**项目汇报.md**: ECFP<br><small>line 5</small> | STALE | Medium | `论文事实.json` @ $.facts[1]<br>`项目汇报.md` @ line 5 | Replace the explicitly superseded name/value in stale materials and retain the confirmed-current source. |
| project_identity.project_name (`fact-b67a638d562dfdec`) | PV-YOLO 光伏缺陷检测 | PV-YOLO 光伏缺陷检测<br><small>$.facts[0]</small> | **项目分工.csv**: —<br>**项目汇报.md**: PV-YOLO 光伏缺陷检测<br><small>line 3</small> | CONSISTENT | Low | `项目汇报.md` @ line 3<br>`论文事实.json` @ $.facts[0] | No repair required; preserve the locator-backed wording. |
| timeline_version.model_version (`fact-53793e4f3c0538b3`) | v2.0 | — | **项目分工.csv**: —<br>**项目汇报.md**: v2.0 [{"dataset": "PV-Synth", "iou_threshold": "0.5", "modality": "RGB", "model_version": "v2.0"}]<br><small>line 7</small> | CONSISTENT | Low | `项目汇报.md` @ line 7 | No repair required; preserve the locator-backed wording. |

## 4. 高风险问题

- **Critical · CONTRADICTED · contribution.personal_contribution (`fact-1f09a0560500f244`)** — Stop using the broader ownership claim; obtain an explicit team/user confirmation before synchronizing contribution wording. Evidence: `论文事实.json` @ $.facts[3]<br>`项目汇报.md` @ line 11<br>`项目分工.csv` @ row 2, column "贡献说明"
- **High · CONTRADICTED · metric.map_0_5 (`fact-ae85323996370485`)** — Do not choose a value automatically; trace the originating result/configuration and obtain confirmation. Evidence: `项目汇报.md` @ line 7<br>`论文事实.json` @ $.facts[2]
- **High · UNSUPPORTED · deployment.deployment_readiness (`fact-b85157af088c39e3`)** — Remove or qualify the claim until a result table, log, calculation, configuration, or explicit record is linked. Evidence: `项目汇报.md` @ line 9
- **High · UNSUPPORTED · metric.performance_improvement (`fact-aca02130d31c96b6`)** — Remove or qualify the claim until a result table, log, calculation, configuration, or explicit record is linked. Evidence: `项目汇报.md` @ line 9

## 5. 建议修复顺序

1. **Critical · CONTRADICTED · personal_contribution** — Stop using the broader ownership claim; obtain an explicit team/user confirmation before synchronizing contribution wording.
2. **High · CONTRADICTED · map_0_5** — Do not choose a value automatically; trace the originating result/configuration and obtain confirmation.
3. **High · UNSUPPORTED · deployment_readiness** — Remove or qualify the claim until a result table, log, calculation, configuration, or explicit record is linked.
4. **High · UNSUPPORTED · performance_improvement** — Remove or qualify the claim until a result table, log, calculation, configuration, or explicit record is linked.
5. **Medium · STALE · module_name** — Replace the explicitly superseded name/value in stale materials and retain the confirmed-current source.

## 6. 未决问题

- **contribution.personal_contribution (`fact-1f09a0560500f244`) · CONTRADICTED** — candidates: 个人工作:独立设计并训练全部模型。 @ evidence-c07622c0d9edaeab; 负责数据整理、标注和部分论文写作 @ evidence-72574fa248e819d3; 负责数据整理、标注和部分写作 @ evidence-cf94c54ca4297cd1. Stop using the broader ownership claim; obtain an explicit team/user confirmation before synchronizing contribution wording.
- **deployment.deployment_readiness (`fact-b85157af088c39e3`) · UNSUPPORTED** — candidates: True @ evidence-bccf37ee56b1941f. Remove or qualify the claim until a result table, log, calculation, configuration, or explicit record is linked.
- **metric.map_0_5 (`fact-ae85323996370485`) · CONTRADICTED** — candidates: 93.7 % @ evidence-91f7039eb59aba39; 94.2 % @ evidence-4bf122ba902a8712. Do not choose a value automatically; trace the originating result/configuration and obtain confirmation.
- **metric.performance_improvement (`fact-aca02130d31c96b6`) · UNSUPPORTED** — candidates: 12 % @ evidence-ba305f3a8a7bf32b. Remove or qualify the claim until a result table, log, calculation, configuration, or explicit record is linked.

## 7. 各材料同步状态

| Material | Referenced facts | Status summary | Explicitly missing facts |
|---|---|---|---|
| 论文事实.json | 4 | CONSISTENT=1, CONTRADICTED=2, STALE=1 | — |
| 项目分工.csv | 1 | CONTRADICTED=1 | — |
| 项目汇报.md | 7 | CONSISTENT=2, CONTRADICTED=2, STALE=1, UNSUPPORTED=2 | — |

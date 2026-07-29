# sync-project-facts

**科研项目事实同步器**：只读核对同一科研项目的多份材料，建立带精确来源定位的事实总账，并生成可执行的同步报告。

## 适用场景

当简历、论文、PPT、README、实验日志、配置、表格或结果摘要对同一项目的指标、数据集、模型名称、版本、时间、部署状态或个人贡献存在差异时使用。至少需要两份属于同一项目的材料。

## 核心能力

- 提取 Markdown、TXT、代码、JSON、YAML、CSV、DOCX、PPTX、XLSX 和 PDF 中的事实候选。
- 保留行号、页码、幻灯片、段落、表格、单元格或键路径等回查位置。
- 识别 `CONSISTENT`、`EQUIVALENT`、`SCOPED_DIFFERENCE`、`STALE`、`CONTRADICTED`、`UNSUPPORTED`、`MISSING` 和 `UNRESOLVED`。
- 保留人工确认的历史裁决；不按多数票、修改时间或更高指标自动选值。
- 默认离线运行，不上传材料，不修改任何源文件。

## 输出

- `project-facts.json`：通过 [JSON Schema](schemas/project-facts.schema.json) 约束的事实总账。
- `fact-sync-report.md`：材料清单、冲突矩阵、高风险问题、修复顺序、未决问题和同步状态。

完整运行流程见 [SKILL.md](SKILL.md)，示例结果见 [examples/pv-yolo-project/output](examples/pv-yolo-project/output)。

## 安全边界

本 Skill 不用于单文档润色、答辩题生成、数据集质量检查、文件筛选、技术路线绘制，也不会编造缺失的实验结果、部署状态或个人贡献。

## 验证

仓库包含22项单元与端到端测试，覆盖中文路径、精确定位、只读性、幂等运行、Schema验证、离线核心流程及触发边界。

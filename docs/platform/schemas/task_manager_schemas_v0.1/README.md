# Task Manager JSON Schema 包（v0.1）

生成时间：2026-03-14T09:32:03Z

本目录提供 Task Manager 首期四个核心工具的 JSON Schema，作为 CLI、MCP 或函数工具的统一契约基线。

## 文件结构

- `common/base.schema.json`：共享定义，包含请求/响应包络、错误对象、locator、任务状态等。
- `tools/memory.search.request.schema.json`
- `tools/memory.search.response.schema.json`
- `tools/memory.read.request.schema.json`
- `tools/memory.read.response.schema.json`
- `tools/task.get.request.schema.json`
- `tools/task.get.response.schema.json`
- `tools/task.patch.request.schema.json`
- `tools/task.patch.response.schema.json`
- `manifest.json`：工具与 schema 的索引文件。

## 设计要点

1. 所有 schema 基于 JSON Schema Draft 2020-12。
2. CLI 只是执行层，正式契约是这些 JSON Schema。
3. 所有工具都复用了统一请求/响应包络。
4. 响应 schema 同时覆盖成功与失败两种包络。
5. `task.patch` 采用操作数组模式，首期支持 `set` / `append` / `remove`。

## 建议接入方式

- CLI：先校验请求 JSON，再调用实现逻辑，最后校验响应 JSON。
- MCP / 函数工具：可直接把 `input` 子对象作为参数约束来源，包络字段由运行时自动填充。
- Task Manager：用 `manifest.json` 维护工具清单，后续再扩展 `tools.list` / `tools.describe`。

## 注意事项

- `memory.search` 中的 `score` 只要求为非负数，不强制归一到 0~1。
- `locator.path` 为必填；页码、章节号、段落号按文档能力选填。
- `task.get` 的 `artifacts` 与 `events` 已定义为结构化数组，但保留 `metadata/data` 扩展位。
- 如需破坏性变更，应升级 `schema_version` 或单独提升工具版本。

## 下一步建议

下一步最合适的是继续补齐：

- `tools.list`
- `tools.describe`
- `task-run-packet.schema.json`
- `doc.* / sheet.* / data.* / email.*` 的首批 schema

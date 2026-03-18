# Task Manager 工具接口规范（v0.1）

> 文档层级：Layer 3（未来平台工具接口规范）
>
> 重要说明：本规范定义的是未来平台工具契约基线。当前仓库尚未实现 `tm memory.*` / `tm task.*` 工具层，但可以在后续桥接中对齐本规范。

更新日期：2026-03-14  
状态：建议作为第一阶段开发基线文档

## 1. 文档目的

本文用于定义 Task Manager 平台的统一工具接口规范，重点解决以下问题：

- 后续新增工具如何保持统一结构。
- Task Manager 如何以程序方式稳定调用工具。
- 工具如何在 CLI、MCP、函数调用等不同运行形式下共享同一套契约。
- 外部记忆系统、任务状态系统和后续各类工具如何在同一框架下扩展。

本文不讨论具体业务算法实现，而是定义工具层的统一约束。

本文是《平台设计总纲》和《平台落地方案》的下位文档。若三者出现冲突，以本规范对“工具接口”的定义为准。

## 2. 适用范围

本规范适用于下列全部工具域：

- `tools.*`：工具发现与描述
- `memory.*`：文档记忆与检索
- `task.*`：任务状态与执行上下文
- `doc.*`：文档生成与整理
- `sheet.*`：表格与 Excel 处理
- `data.*`：数据清洗、统计、图表
- `email.*`：邮件摘要、提取、草稿

首期必须落地的最小工具集为：

- `tools.list`
- `tools.describe`
- `memory.search`
- `memory.read`
- `task.get`
- `task.patch`

其他工具域可以后续逐步接入，但一旦接入，必须遵守本规范。

## 3. 设计原则

### 3.1 协议优先

系统首先统一输入输出契约，其次才是工具内部实现。只要接口契约稳定，工具底层可以逐步替换和升级。

### 3.2 CLI 只是执行适配层

所有工具可以通过统一 CLI 入口执行，但 CLI 只是传输和适配方式，不是平台真正依赖的抽象。平台真正依赖的是统一 JSON schema。

### 3.3 结果与日志分离

工具的机器可读结果必须与运行日志严格分离。标准结果只能出现在 `stdout`，日志只能出现在 `stderr` 或独立日志系统中。

### 3.4 可追溯

涉及文档、状态、生成结果的工具，应尽可能返回足够的标识、定位和来源信息，确保后续可以回溯。

### 3.5 可扩展

接口设计必须支持后续新增工具域、字段和返回信息，而不需要推翻已接入工具。

## 4. 约束词说明

本文使用以下约束词：

- “必须”：表示强制要求。
- “应当”：表示推荐要求，除非有明确理由例外。
- “可以”：表示可选设计。

## 5. 命名与命名空间

### 5.1 顶层命令格式

统一命令格式为：

```text
tm <domain> <action>
```

示例：

```text
tm memory search
tm memory read
tm task get
tm task patch
tm tools list
tm tools describe
```

### 5.2 域名规范

域名使用小写英文单数名词，建议控制在 2 到 12 个字符之间。

保留域名：

- `tools`
- `memory`
- `task`
- `doc`
- `sheet`
- `data`
- `email`

### 5.3 动作名规范

动作名使用小写英文动词，优先使用高复用基础动作。

首选动作示例：

- `list`
- `describe`
- `search`
- `read`
- `get`
- `patch`
- `generate`
- `transform`
- `analyze`
- `draft`

避免使用含义模糊或过宽的动作名，例如：

- `run`
- `exec`
- `process`
- `do`

### 5.4 正式工具标识

平台内部统一使用 `<domain>.<action>` 表示工具标识，例如：

- `memory.search`
- `task.patch`
- `sheet.transform`

## 6. 统一调用模型

平台工具应当同时区分三层概念：

第一层，**逻辑工具**。即 `memory.search`、`task.patch` 这类正式能力名称。

第二层，**执行适配**。即 CLI、MCP server、函数调用包装器等调用方式。

第三层，**底层实现**。即脚本、库、服务、数据库、检索系统等具体实现。

推荐关系如下：

- 逻辑工具：平台契约层
- CLI：本地执行适配层
- MCP / function tool：供 Codex 或其他代理调用的暴露层
- Python/Go/Node 库或服务：工具内部实现层

结论是：

**应当统一逻辑工具契约，而不是把“某个 shell 命令”当作最终抽象。**

## 7. 标准输入输出协议

### 7.1 请求包络

所有工具请求都应使用统一请求包络。

标准结构如下：

```json
{
  "schema_version": "1.0",
  "request_id": "req-20260314-001",
  "trace_id": "trace-20260314-001",
  "task_id": "task-20260314-001",
  "caller": "task_manager",
  "input": {}
}
```

字段说明：

- `schema_version`：请求 schema 版本，首期固定为 `1.0`。
- `request_id`：本次工具调用唯一标识，必须由调用方提供或由工具自动生成后回传。
- `trace_id`：同一任务链路的追踪标识，应当在多次调用中保持一致。
- `task_id`：所属任务标识；若当前调用与任务无关，可以为空。
- `caller`：调用者标识，例如 `task_manager`、`manual_cli`、`worker`。
- `input`：动作级输入载荷。

### 7.2 响应包络

所有工具响应都必须使用统一响应包络。

标准结构如下：

```json
{
  "schema_version": "1.0",
  "request_id": "req-20260314-001",
  "tool": "memory.search",
  "status": "ok",
  "data": {},
  "error": null,
  "meta": {
    "duration_ms": 128,
    "warnings": []
  }
}
```

失败时：

```json
{
  "schema_version": "1.0",
  "request_id": "req-20260314-001",
  "tool": "memory.search",
  "status": "error",
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "input.query is required",
    "retryable": false,
    "details": {
      "field": "input.query"
    }
  },
  "meta": {
    "duration_ms": 12,
    "warnings": []
  }
}
```

### 7.3 响应字段要求

- `schema_version` 必须存在。
- `request_id` 必须原样返回。
- `tool` 必须为正式工具标识。
- `status` 只能取 `ok` 或 `error`。
- `data` 与 `error` 必须互斥。
- `meta.duration_ms` 应当返回。
- `meta.warnings` 可以为空数组，但字段应当保留。

## 8. CLI 运行规范

### 8.1 输入方式

CLI 必须至少支持以下一种输入方式：

```text
tm <domain> <action> --input-json request.json
```

建议同时支持：

```text
cat request.json | tm <domain> <action> --stdin
```

### 8.2 输出方式

- `stdout` 只能输出最终 JSON 响应包络。
- `stderr` 只能输出日志、调试信息或告警。
- 不允许在 `stdout` 混入说明文字、进度条或彩色日志。

### 8.3 非交互性要求

默认工具必须可在无人值守模式下运行，不允许在执行期间主动弹出交互问题。

### 8.4 Exit Code 规范

建议统一如下：

- `0`：成功
- `2`：输入校验失败
- `3`：目标不存在
- `4`：状态冲突
- `5`：权限不足
- `6`：外部依赖不可用
- `7`：超时
- `10`：未分类内部错误

说明：即使返回非 0 exit code，若能构造标准 JSON 错误包络，仍应优先返回包络。

## 9. 错误模型

### 9.1 标准错误结构

`error` 对象结构如下：

```json
{
  "code": "VALIDATION_ERROR",
  "message": "input.query is required",
  "retryable": false,
  "details": {}
}
```

字段说明：

- `code`：稳定的机器可读错误码。
- `message`：面向人类的简洁错误说明。
- `retryable`：是否建议重试。
- `details`：附加结构化信息。

### 9.2 标准错误码族

建议统一使用下列错误码前缀：

- `VALIDATION_*`：输入校验失败
- `NOT_FOUND_*`：对象不存在
- `CONFLICT_*`：状态冲突
- `PERMISSION_*`：权限或访问限制
- `DEPENDENCY_*`：外部依赖不可用
- `TIMEOUT_*`：超时
- `INTERNAL_*`：内部未分类错误

首期至少保留以下通用错误码：

- `VALIDATION_ERROR`
- `NOT_FOUND`
- `CONFLICT`
- `PERMISSION_DENIED`
- `DEPENDENCY_UNAVAILABLE`
- `TIMEOUT`
- `INTERNAL_ERROR`

## 10. 工具发现接口

为保证后续可扩展性，平台首期应当提供工具注册表能力。

### 10.1 `tools.list`

作用：返回当前环境中可用的逻辑工具清单。

输入：

```json
{
  "schema_version": "1.0",
  "request_id": "req-001",
  "trace_id": "trace-001",
  "task_id": null,
  "caller": "task_manager",
  "input": {}
}
```

输出示例：

```json
{
  "schema_version": "1.0",
  "request_id": "req-001",
  "tool": "tools.list",
  "status": "ok",
  "data": {
    "tools": [
      {
        "name": "memory.search",
        "version": "1.0",
        "domain": "memory",
        "action": "search",
        "implemented": true
      },
      {
        "name": "task.patch",
        "version": "1.0",
        "domain": "task",
        "action": "patch",
        "implemented": true
      }
    ]
  },
  "error": null,
  "meta": {
    "duration_ms": 8,
    "warnings": []
  }
}
```

### 10.2 `tools.describe`

作用：返回指定工具的接口说明，供 Task Manager 或 Codex 在运行时确认参数与返回结构。

输入示例：

```json
{
  "schema_version": "1.0",
  "request_id": "req-002",
  "trace_id": "trace-001",
  "task_id": null,
  "caller": "task_manager",
  "input": {
    "tool": "memory.search"
  }
}
```

输出应至少包含：

- 工具名称
- 版本号
- 输入字段说明
- 输出字段说明
- 错误码说明
- 是否幂等
- 是否只读

## 11. `memory.*` 接口规范

### 11.1 设计目标

`memory.*` 用于向 Task Manager 暴露外部文档记忆能力。首期必须至少支持“搜索”和“读取”。

记忆系统的首期目标不是做完美知识库，而是为任务执行提供：

- 候选内容召回
- 原文片段读取
- 基本过滤能力
- 引用定位信息

### 11.2 `memory.search`

作用：根据查询条件返回候选文档片段列表。

#### 输入

```json
{
  "schema_version": "1.0",
  "request_id": "req-100",
  "trace_id": "trace-100",
  "task_id": "task-100",
  "caller": "task_manager",
  "input": {
    "query": "设备 X 校准条件",
    "filters": {
      "doc_type": ["report", "meeting_note"],
      "project": ["projectA"],
      "date_from": "2023-01-01",
      "date_to": "2025-12-31",
      "tags": ["calibration"]
    },
    "top_k": 8,
    "retrieval_mode": "hybrid"
  }
}
```

#### 输入字段要求

- `input.query` 应当为非空字符串。
- `input.filters` 可以为空对象。
- `input.top_k` 应当为正整数；首期建议默认 `10`，上限建议 `50`。
- `input.retrieval_mode` 可以取 `keyword`、`semantic`、`hybrid`；首期建议默认 `hybrid`。

#### 首期过滤字段

首期至少支持：

- `doc_type`
- `project`
- `date_from`
- `date_to`
- `tags`

建议预留但可暂不实现：

- `author`
- `version`
- `status`
- `source_path`

#### 输出

```json
{
  "schema_version": "1.0",
  "request_id": "req-100",
  "tool": "memory.search",
  "status": "ok",
  "data": {
    "results": [
      {
        "doc_id": "DOC-017",
        "chunk_id": "c-12",
        "title": "项目 A 技术报告",
        "doc_type": "report",
        "project": "projectA",
        "date": "2024-09-18",
        "score": 0.87,
        "snippet": "……",
        "locator": {
          "path": "/docs/projectA/report.pdf",
          "page": 14,
          "section": "3.2"
        },
        "attributes": {
          "tags": ["calibration"]
        }
      }
    ],
    "total_returned": 1
  },
  "error": null,
  "meta": {
    "duration_ms": 130,
    "warnings": []
  }
}
```

#### 返回字段要求

每个结果项至少应包含：

- `doc_id`
- `chunk_id`
- `title`
- `score`
- `snippet`
- `locator`

`locator` 应当尽量返回可追溯信息，例如：

- 文档路径
- 页码
- 章节号
- 段落号

如果某类文档无法提供页码，也应尽量返回路径与块级标识。

### 11.3 `memory.read`

作用：读取指定文档或指定片段的完整内容。

#### 输入

```json
{
  "schema_version": "1.0",
  "request_id": "req-101",
  "trace_id": "trace-100",
  "task_id": "task-100",
  "caller": "task_manager",
  "input": {
    "doc_id": "DOC-017",
    "chunk_id": "c-12"
  }
}
```

#### 输入字段要求

- `doc_id` 必须存在。
- `chunk_id` 可以为空；若为空，则表示读取整个文档或默认主内容。

#### 输出

```json
{
  "schema_version": "1.0",
  "request_id": "req-101",
  "tool": "memory.read",
  "status": "ok",
  "data": {
    "doc_id": "DOC-017",
    "chunk_id": "c-12",
    "title": "项目 A 技术报告",
    "content_type": "text/plain",
    "content": "完整片段内容",
    "locator": {
      "path": "/docs/projectA/report.pdf",
      "page": 14,
      "section": "3.2"
    },
    "attributes": {
      "doc_type": "report",
      "project": "projectA"
    }
  },
  "error": null,
  "meta": {
    "duration_ms": 24,
    "warnings": []
  }
}
```

#### 读取要求

- `memory.read` 应当返回足以生成引用的定位信息。
- 对超长内容，可以增加截断或分页机制，但首期建议优先面向“chunk 级读取”。
- 如果请求的 `chunk_id` 不存在，应返回 `NOT_FOUND` 类错误。

## 12. `task.*` 接口规范

### 12.1 设计目标

`task.*` 用于保存和读取任务状态，解决“这个任务做到哪里”的问题。

首期最小目标是让 Task Manager 能够读取当前任务状态，并写回状态更新与关键结果。

### 12.2 `task.get`

作用：读取任务状态。

#### 输入

```json
{
  "schema_version": "1.0",
  "request_id": "req-200",
  "trace_id": "trace-200",
  "task_id": "task-200",
  "caller": "task_manager",
  "input": {
    "task_id": "task-200"
  }
}
```

#### 输出

```json
{
  "schema_version": "1.0",
  "request_id": "req-200",
  "tool": "task.get",
  "status": "ok",
  "data": {
    "task_id": "task-200",
    "status": "running",
    "goal": "总结项目 A 的设备 X 校准条件",
    "current_step": "reading_memory",
    "context_summary": "已完成候选文档检索，正在读取前 3 个片段。",
    "artifacts": [],
    "events": [],
    "updated_at": "2026-03-14T08:00:00Z"
  },
  "error": null,
  "meta": {
    "duration_ms": 10,
    "warnings": []
  }
}
```

#### 状态枚举建议

首期建议统一使用以下状态：

- `draft`
- `running`
- `waiting_input`
- `succeeded`
- `failed`
- `canceled`

### 12.3 `task.patch`

作用：更新任务状态。

为避免首期实现过重，本规范建议采用“操作数组”模式，而不是整对象覆盖。

#### 输入

```json
{
  "schema_version": "1.0",
  "request_id": "req-201",
  "trace_id": "trace-200",
  "task_id": "task-200",
  "caller": "task_manager",
  "input": {
    "task_id": "task-200",
    "ops": [
      {
        "op": "set",
        "path": "status",
        "value": "running"
      },
      {
        "op": "set",
        "path": "current_step",
        "value": "summarizing"
      },
      {
        "op": "append",
        "path": "events",
        "value": {
          "type": "memory_search_completed",
          "at": "2026-03-14T08:01:00Z",
          "summary": "已检索到 8 个候选片段。"
        }
      }
    ]
  }
}
```

#### 支持的操作类型

首期建议支持：

- `set`
- `append`
- `remove`

#### 输出

```json
{
  "schema_version": "1.0",
  "request_id": "req-201",
  "tool": "task.patch",
  "status": "ok",
  "data": {
    "task_id": "task-200",
    "updated": true,
    "updated_at": "2026-03-14T08:01:00Z"
  },
  "error": null,
  "meta": {
    "duration_ms": 14,
    "warnings": []
  }
}
```

#### 状态写回建议

首期建议最少允许写回以下内容：

- `status`
- `current_step`
- `context_summary`
- `events`
- `artifacts`
- `result_summary`

## 13. 任务包规范

由于当前架构中每次可能新拉起一个 Codex 或新的执行实例，因此 Task Manager 不应依赖“模型自然记住上一次内容”，而应在每次调用前构造标准任务包。

推荐任务包结构如下：

```json
{
  "run_id": "run-20260314-001",
  "task_id": "task-20260314-001",
  "goal": "总结项目 A 中设备 X 校准条件",
  "workflow_id": "memory_summary_v1",
  "allowed_tools": [
    "memory.search",
    "memory.read",
    "task.get",
    "task.patch"
  ],
  "constraints": {
    "must_cite_sources": true,
    "max_memory_hits": 8
  },
  "state_ref": {
    "task_id": "task-20260314-001"
  },
  "input": {
    "project": "projectA",
    "topic": "设备 X 校准条件"
  },
  "output_contract": {
    "type": "summary_with_citations"
  }
}
```

任务包不是底层工具接口的一部分，但应当被视为 Task Manager 与代理执行器之间的标准输入结构。

## 14. 可扩展工具域的接入要求

后续新增 `doc.*`、`sheet.*`、`data.*`、`email.*` 时，必须遵守以下要求：

### 14.1 命名要求

- 必须使用 `<domain>.<action>` 工具标识。
- 必须避免含义过宽的“万能工具”。
- 同一工具只负责一个稳定职责。

### 14.2 输入输出要求

- 必须复用统一请求包络与响应包络。
- 必须返回稳定的机器可读结果。
- 必须提供明确错误码。

### 14.3 权限与安全要求

- 默认不允许暴露任意 shell 执行能力。
- 工具调用应当通过白名单控制。
- 涉及文件路径、网络、邮件发送等高风险能力时，应当独立建工具，不得隐藏在泛化工具中。

## 15. 日志、审计与追踪

### 15.1 基本要求

每次工具调用应当可通过以下标识追踪：

- `request_id`
- `trace_id`
- `task_id`
- `tool`

### 15.2 日志建议

日志至少应包含：

- 时间戳
- 日志级别
- 请求标识
- 事件摘要

### 15.3 审计边界

涉及状态更新、文件写入、邮件发送等操作的工具，应当在状态系统或日志系统中留下明确记录。

## 16. 版本与兼容性策略

### 16.1 Schema 版本

本规范首期版本为 `1.0`。

### 16.2 兼容原则

- 新增可选字段应视为向后兼容。
- 删除字段或改变字段含义视为破坏性变更。
- 若发生破坏性变更，应升级主版本。

### 16.3 工具版本

每个工具应当具备自己的版本号，并可通过 `tools.describe` 查询。

## 17. 第一阶段最低实现要求

若要认定平台工具层已进入可开发状态，至少应满足以下条件：

1. `tools.list` 可返回工具清单。  
2. `tools.describe` 可返回接口说明。  
3. `memory.search` 可根据 query + filters 返回候选片段。  
4. `memory.read` 可读取指定片段并返回定位信息。  
5. `task.get` 可读取任务状态。  
6. `task.patch` 可写回状态更新。  
7. 所有工具都使用统一请求和响应包络。  
8. 所有工具都能在 `stdout` 返回纯 JSON。  
9. 所有工具都具备基础错误码。  
10. Task Manager 可基于这些工具跑通首个闭环任务。  

## 18. 推荐的首期工具清单

首期建议只开放以下工具给 Task Manager：

- `tools.list`
- `tools.describe`
- `memory.search`
- `memory.read`
- `task.get`
- `task.patch`

推荐理由是：

- 工具面足够小，便于验证调度逻辑。
- 已能覆盖“检索 → 读取 → 汇总 → 写回状态”的最小闭环。
- 后续可在不破坏核心结构的前提下继续扩展。

## 19. 结论

本规范的核心结论如下：

第一，所有工具可以统一为 `tm <domain> <action>` 形式，但控制台命令只是执行适配层，不是平台最终抽象。  
第二，平台真正需要冻结的是统一 JSON schema、错误模型、命名空间和任务包。  
第三，外部记忆系统首期至少要支持 `search + read` 两步，而不能只有“类型 / 时期 / 关键词检索”。  
第四，Task Manager 的可扩展性不来自提示词堆叠，而来自工具契约稳定、状态外部化和记忆系统标准化。  

一句话概括：

**平台先统一工具契约，再统一工具实现；先建立最小工具面，再逐步扩展工具族。**

# Task Manager 平台落地方案（v0.2）

> 文档层级：Layer 3（未来平台首期落地方案）
>
> 重要说明：该文档描述未来平台第一阶段的最小落地闭环，不应直接视为 `mail_based_task_manager` 当前仓库的实施清单。

更新日期：2026-03-14
状态：用于第一阶段开发与接口收敛

## 1. 文档目的

本文用于将《平台设计总纲》转化为第一阶段可执行的最小落地方案，重点定义首期范围、接口约束、最小闭环、验收标准和实施顺序。

本文适合作为当前开发阶段的工作文档，会随着实现进展持续迭代。

## 2. 第一阶段目标

第一阶段的目标不是“做出完整平台”，而是建立一个真实可运行的最小闭环，使 Task Manager 能够：

1. 接收与历史资料有关的任务。
2. 调用外部记忆系统完成检索。
3. 读取关键文档片段。
4. 基于原文生成摘要或整理结果。
5. 输出带引用的信息。
6. 写回任务状态。

只要这一闭环可稳定运行，平台就具备了最基础的生产价值。

## 3. 首期范围

### 3.1 首期要做的事

- 定义统一工具命名体系
- 定义统一 JSON 输入输出协议
- 建立 memory 与 task 两组核心接口
- 选择一小批高价值文档建立试点索引
- 让 Task Manager 围绕这些接口跑通最小闭环

### 3.2 首期不做的事

- 不一次性导入全部历史文档
- 不追求完善的权限系统
- 不接入全部工具域
- 不追求全自动任务闭环
- 不在首期完成复杂工作流编排

## 4. 第一阶段系统组成

首期系统由四部分组成：

### 4.1 Task Manager

负责接收任务、规划执行步骤、调用工具、汇总结果、更新状态。

### 4.2 外部记忆系统

负责文档解析、索引、检索、片段读取和引用定位。

### 4.3 工具执行系统

负责提供标准化的 memory 和 task 工具入口，后续再扩展到 doc、sheet、data、email 等域。

### 4.4 状态系统

负责保存任务状态、上下文摘要、中间结果和执行记录。

## 5. 首期最小闭环

建议首期闭环定义为：

用户提出一个与历史资料有关的任务；Task Manager 调用 memory.search 检索候选片段；随后调用 memory.read 读取关键内容；生成带引用的摘要；最后使用 task.patch 更新任务状态。

一个可直接用于验证的样例任务是：

“总结项目 A 中所有关于设备 X 校准条件的历史记录，并给出引用来源。”

## 6. 统一工具入口约定

首期建议采用统一顶层命令 `tm` 作为底层执行入口。

命名结构为：

```text
tm <domain> <action>
```

首期至少保留下列命令位：

```text
tm memory search
tm memory read
tm task get
tm task patch
```

后续扩展时使用同一命名风格，例如：

```text
tm doc generate
tm sheet transform
tm data analyze
tm email draft
```

这里需要明确：

**CLI 是执行适配层，不是平台真正的契约层。平台真正依赖的是 JSON schema。**

## 7. CLI 运行约束

为了保证后续可维护性，首期就应统一以下规则：

- 输入只接受 JSON
- 结果只从 stdout 输出 JSON
- 日志只写入 stderr
- 成功和失败使用固定 exit code
- 所有返回都带 `schema_version`
- 所有工具都返回 `status`
- 任何错误都必须返回可解释结构

建议统一采用如下运行风格：

```text
tm memory search --input-json request.json
```

或者：

```text
cat request.json | tm memory search --stdin
```

## 8. 通用返回包络

建议所有工具统一使用如下包络：

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "request_id": "req-001",
  "data": {},
  "error": null
}
```

失败时：

```json
{
  "schema_version": "1.0",
  "status": "error",
  "request_id": "req-001",
  "data": null,
  "error": {
    "code": "MEMORY_NOT_FOUND",
    "message": "document or chunk not found"
  }
}
```

## 9. memory 接口定义（首期最小版）

### 9.1 `tm memory search`

作用：根据 query 和 filters 检索候选文档片段。

输入建议：

```json
{
  "query": "设备 X 校准条件",
  "filters": {
    "doc_type": ["report", "meeting_note"],
    "project": ["projectA"],
    "date_from": "2023-01-01",
    "date_to": "2025-12-31",
    "tags": ["calibration"]
  },
  "top_k": 8
}
```

输出建议：

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "request_id": "req-001",
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
        }
      }
    ]
  },
  "error": null
}
```

### 9.2 `tm memory read`

作用：读取指定文档或指定片段的完整内容。

输入建议：

```json
{
  "doc_id": "DOC-017",
  "chunk_id": "c-12"
}
```

输出建议：

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "request_id": "req-002",
  "data": {
    "doc_id": "DOC-017",
    "chunk_id": "c-12",
    "title": "项目 A 技术报告",
    "content": "完整片段内容",
    "locator": {
      "path": "/docs/projectA/report.pdf",
      "page": 14,
      "section": "3.2"
    }
  },
  "error": null
}
```

### 9.3 首期检索字段

首期至少支持以下过滤字段：

- `doc_type`
- `project`
- `date_from`
- `date_to`
- `tags`

建议从 schema 一开始就预留但可暂不实现的字段：

- `version`
- `author`
- `status`
- `source_path`

## 10. task 接口定义（首期最小版）

### 10.1 `tm task get`

作用：读取指定任务当前状态。

输入建议：

```json
{
  "task_id": "task-20260314-001"
}
```

### 10.2 `tm task patch`

作用：对任务状态进行部分更新。

输入建议：

```json
{
  "task_id": "task-20260314-001",
  "patch": {
    "stage": "summarized",
    "memory_refs": [
      {"doc_id": "DOC-017", "chunk_id": "c-12"}
    ],
    "summary": "已完成第一轮摘要"
  }
}
```

## 11. Task Manager 到 Codex 的任务包建议

由于执行器可能是无状态新实例，首期就应采用结构化任务包，而不是依赖长提示词复述上下文。

建议任务包至少包含：

```json
{
  "policy_version": "v0.1",
  "task_id": "task-20260314-001",
  "goal": "总结项目 A 中与设备 X 校准条件相关的历史记录",
  "allowed_tools": [
    "memory.search",
    "memory.read",
    "task.get",
    "task.patch"
  ],
  "state_summary": "尚未开始",
  "output_schema": "summary_with_citations_v1"
}
```

这可以显著降低“每次新拉起执行器后重新猜上下文”的不稳定性。

## 12. 推荐实施顺序

建议按如下顺序推进：

### 第一步：冻结接口契约

先定义 `memory.search`、`memory.read`、`task.get`、`task.patch` 的 schema。

### 第二步：建立试点文档集

只选一小批高价值文档作为首期数据源，例如某个项目的报告、会议纪要和实验记录。

### 第三步：实现简化后端

哪怕首版只是简单索引，也要先保证 search 和 read 可用，且返回结构稳定。

### 第四步：实现 CLI 包装层

让 `tm` 命令能够稳定接收 JSON 并返回 JSON。

### 第五步：接入 Task Manager

让 Task Manager 能围绕既定接口完成一次完整调用链。

### 第六步：建立验收样例

用固定任务样例反复测试，检查检索、引用、摘要和状态更新是否稳定。

## 13. 首期验收标准

首期建议至少满足以下条件：

- 能对试点文档集建立可用索引
- `memory.search` 可返回带 `doc_id` 与 `chunk_id` 的候选结果
- `memory.read` 可读取指定片段并返回定位信息
- Task Manager 能基于检索结果生成带引用摘要
- `task.patch` 能记录执行结果和引用来源
- 同一任务多次运行时，输出结构保持稳定

## 14. 推荐目录结构（示意）

```text
platform/
  docs/
    task_manager_platform_design.md
    task_manager_platform_delivery.md
    task_manager_implementation_roadmap.md
    task_manager_tool_interface_spec.md
  tools/
    tm/
  memory/
    raw_docs/
    parsed/
    index/
  state/
    tasks/
    logs/
  examples/
    requests/
    outputs/
```

## 15. 下一阶段扩展方向

当首期闭环稳定后，再按优先级扩展：

1. `doc.*` 文档生成工具
2. `sheet.*` Excel 与表格工具
3. `data.*` 数据分析工具
4. `email.*` 邮件相关工具
5. 工作流模板与批处理能力

## 16. 当前阶段结论

当前最重要的不是继续增加零散工具，也不是打磨更长的提示词，而是先固定首期契约。

首期真正需要冻结的是三件事：

- 平台顶层边界
- 统一工具协议
- 外部记忆与任务状态的最小接口

只要这三件事定下来，后续工具数量增加时，平台仍然能保持结构稳定。

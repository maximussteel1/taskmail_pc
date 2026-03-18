# Run Artifact Delivery Plan

> 文档层级：Layer 2（当前仓库改造计划）
>
> 适用范围：`mail_based_task_manager` 当前仓库本体。
> 本文只规划当前仓库里的本地文件交付与产物收口，不把本仓库扩写成完整附件平台或未来 Task Manager 平台。

## 0. 当前基线

- 评估日期：2026-03-15
- 相关当前协议：`docs/current/multimedia_mail_protocol.md`
- 相关现有模块：`mail_runner/adapters/cli_common.py`、`mail_runner/artifact_resolver.py`、`mail_runner/reporter.py`、`mail_runner/mail_io.py`
- 当前进展：
  - `RunArtifact` 内部模型已落地
  - `artifact_index.json` sidecar 已落地
  - mail projection 仍兼容 `OutgoingAttachment`
- 当前事实：
  - inbound attachment 已可落到 active `workdir`
  - backend 已可通过 `MAIL_RUNNER_ARTIFACTS_DIR` 产出本次运行文件
  - backend 已可通过 `manifest.json` 显式声明绝对路径文件
  - mail 层已可把图片作为附件发送，并在 HTML 中做 inline preview

当前缺口不在“能不能发文件”，而在“文件产物的架构边界还不够明确”。

目前的主要问题是：

1. runtime 已经有 `artifacts_dir` 概念，但解析结果直接落成 `OutgoingAttachment`，仍然偏 mail 语义。
2. run 级产物缺少一个稳定、可检查、可复用的 sidecar 索引。
3. 未来如果要在邮件之外复用这些文件，当前结构会让 `artifact_resolver` 和 mail delivery 绑得过紧。
4. 当前 inline 图片已经能通过邮件 HTML 发出去，但还没有被收口成可跨渠道复用的“渲染意图”。

## 1. 文档目的

本文要解决的是一个很具体的问题：

**在“只考虑本地工作区”的前提下，如何让 Codex / OpenCode 产出的文件成为一个稳定的一等运行产物，而不是只在发邮件时临时拼出来的附件。**

本轮目标不是做复杂附件系统，而是把当前已存在的出站文件能力收口成一个更稳定的本地协议。

## 2. 设计目标

本方案优先考虑三件事：

1. 稳定性：不破坏当前 runner、adapter、thread/session 持久化主结构。
2. 易用性：agent 默认只需要“写文件到 `artifacts/`”，不需要每次都写复杂协议。
3. 扩展性：未来即使增加 Web UI、本地查看器或其他交付渠道，也不必重写 backend 与 run 产物契约。

## 3. 非目标

本方案明确不做以下事情：

1. 不引入对象存储、下载链接或多租户权限模型。
2. 不根据自由文本猜测“这段话是不是应该变成附件”。
3. 不要求新增第二套 agent 控制协议。
4. 不把当前仓库改造成通用文件平台。

## 4. 核心判断

当前仓库里最合适的抽象不是“邮件附件”，而是：

**run artifact**

也就是：

- 它首先是“本次运行产出的文件”
- 然后才可能被某个 delivery adapter 转换成“邮件附件”

因此建议把职责拆成三层：

1. backend 只负责产出文件，并可选写 manifest。
2. runtime 负责解析 run artifact，并写出可检查的 artifact index。
3. mail 层只负责把 run artifact 投递成邮件附件/inline 图片。

## 5. 建议架构

### 5.1 Canonical 目录边界

每次 run 继续使用现有目录：

```text
tasks/
  thread_001/
    runs/
      <task_id>/
        artifacts/
          manifest.json
          artifact_index.json
          ...
```

约定如下：

- `artifacts/` 仍是本次运行的 canonical artifact root
- `manifest.json` 继续作为显式声明协议，优先级最高
- `artifact_index.json` 由 runtime 生成，作为本次解析后的 run artifact 真相源
- `artifact_index.json` 和 `manifest.json` 本身不视为用户交付文件

### 5.2 产物模型分层

建议在 mail 语义之外，引入一个内部领域模型 `RunArtifact`。

推荐字段方向：

```python
RunArtifact(
    artifact_id: str,
    path: str,
    name: str,
    kind: str,             # image | file
    content_type: str,
    source: str,           # directory_fallback | manifest
    attach: bool,
    inline_preview: bool,
    caption: str | None,
)
```

边界要求：

- `RunArtifact` 是 runtime 内部模型
- `OutgoingAttachment` 是 mail delivery 模型
- `RunArtifact -> OutgoingAttachment` 的转换应在 mail adapter 边界发生

这样做的意义是：

- `artifact_resolver` 不再直接依赖邮件发送语义
- 未来如果增加非邮件交付渠道，可以复用同一份 run artifact 结果

### 5.3 artifact index sidecar

建议每次 run 在 artifact 解析后生成：

```json
{
  "version": 1,
  "task_id": "task_001",
  "artifacts_root": "runs/task_001/artifacts",
  "source": "manifest",
  "items": [
    {
      "artifact_id": "artifact-chart",
      "path": "E:\\repo\\runs\\task_001\\artifacts\\chart.png",
      "name": "chart.png",
      "kind": "image",
      "content_type": "image/png",
      "attach": true,
      "inline_preview": true,
      "caption": "Result chart"
    }
  ],
  "skipped": []
}
```

这份 sidecar 的作用不是给 agent 读，而是给 runtime、调试、后续 UI 和未来渠道读。

关于 `artifact_id`、`kind`、Markdown 引用协议和 renderer 分层的更具体方案，见 `docs/plans/artifact_markdown_rendering_plan.md`。

### 5.4 delivery adapter 边界

在这版方案里，mail 继续只是一个 delivery adapter。

职责建议如下：

- `artifact_resolver.py`：解析 manifest / fallback，产出 `RunArtifact`
- `reporter.py`：根据 run artifact 生成 artifact summary 与 inline preview 所需信息
- `mail_io.py`：把 run artifact 转成邮件附件 MIME part
- `app.py`：在发送状态邮件前串起 artifact resolve -> index persist -> mail projection

如果未来目标是 Markdown-first 渲染，则 `reporter.py` 应先生成 canonical Markdown body，再按渠道做投影；详见 `docs/plans/artifact_markdown_rendering_plan.md`。

## 6. 关键稳定性选择

为了不破坏现有架构，第一版建议明确以下约束：

1. 不修改 `WorkerAdapter.run()` 的输入输出签名。
2. `RunResult` 继续只保留 `artifacts_dir`，不直接塞完整 artifact 列表。
3. `ThreadState` / `SessionState` 不持久化 artifact 明细，避免状态膨胀。
4. `manifest.json` 继续支持绝对路径，但只允许显式声明，不做任意目录扫描。
5. 无效文件继续走 skipped note，不让整次状态邮件失败。

这些约束能保证：

- 当前调度器、恢复逻辑、session 控制逻辑几乎不用动
- run artifact 成为可演进的新边界，但不会反向污染核心状态模型

## 7. 关键易用性选择

为了保持使用门槛低，建议给 agent 一条非常简单的默认路径：

1. 想发文件：直接写到 `MAIL_RUNNER_ARTIFACTS_DIR`
2. 只有当文件不在该目录、或需要重命名/inline/caption 时，才写 `manifest.json`

对应的用户体验收益：

- agent 默认不用理解复杂协议
- 本地调试时直接看 `artifacts/` 就能知道本次产出了什么
- 显式场景仍可通过 manifest 控制更细行为

## 8. 关键扩展性选择

这个方案的核心扩展点是 `artifact_index.json` 和 `RunArtifact`，而不是邮件本身。

它为后续预留了三种扩展空间：

1. 可新增本地查看器或任务面板，直接读取 `artifact_index.json`
2. 可新增非邮件 delivery adapter，而无需改 backend 产物协议
3. 可在未来 typed result / result envelope 中引用 artifact index，而不是重新扫描目录

也就是说，未来要扩展的是“交付层”，不是“产物协议”。

## 9. 分阶段推进建议

### 阶段 1：冻结本地 artifact 契约

目标：

- 先把 `artifacts/`、`manifest.json`、`artifact_index.json` 的职责写清楚
- 保持现有邮件能力可用

建议动作：

- 先以文档形式固定 run artifact 边界
- 在现有 `artifact_resolver` 旁边补 artifact index 写入能力

### 阶段 2：把 artifact resolver 从 mail 语义里解耦

目标：

- `artifact_resolver` 改为产出 `RunArtifact`
- mail 层再投影成 `OutgoingAttachment`

建议动作：

- 新增内部 artifact 模型
- 收口 resolver 与 reporter 的接口

### 阶段 3：增强用户可见反馈

目标：

- 状态邮件正文里增加一小段 artifact summary
- 让手机端用户不打开附件也能先知道本次带回了什么

建议动作：

- reporter 渲染 artifact summary
- skipped note 与 artifact summary 使用同一份 index 数据

## 10. 模块落点

建议按以下模块收口：

- `mail_runner/adapters/cli_common.py`
  - 继续向 backend 暴露 `MAIL_RUNNER_ARTIFACTS_DIR`
  - 保持“目录优先，manifest 补充”的 prompt/runtime 提示

- `mail_runner/models.py`
  - 后续可新增 `RunArtifact`
  - 保持 `OutgoingAttachment` 作为 mail delivery 模型

- `mail_runner/artifact_resolver.py`
  - 从“附件解析器”收口成“run artifact 解析器”
  - 负责输出 sidecar index 所需数据

- `mail_runner/reporter.py`
  - 负责用户可见的 artifact summary 与 inline preview 说明

- `mail_runner/mail_io.py`
  - 只负责 delivery，不承担 artifact 发现职责

- `mail_runner/app.py`
  - 负责在发送状态邮件前组织 resolve / index / projection 流程

## 11. 验收标准

第一版认定完成时，至少应满足：

1. backend 只写 `artifacts/` 目录时，文件仍可被稳定投递。
2. backend 写 `manifest.json` 且引用绝对路径时，文件仍可被稳定投递。
3. 每次有出站文件的 run，都能产出 `artifact_index.json`。
4. `artifact_index.json` 能记录解析出的文件、`artifact_id`、`kind` 与 skipped 项。
5. mail 投递仍然只消费显式解析结果，不做任意猜测。
6. `ThreadState` / `SessionState` / `RunResult` 的主结构不因 artifact 明细而膨胀。
7. 现有 inbound attachment 流程保持不变。

## 12. 当前建议

当前最推荐的推进顺序不是“直接重构 resolver”，而是：

1. 先把 run artifact 边界以文档方式冻结
2. 再补 `artifact_index.json`
3. 然后再做 `RunArtifact` 与 mail projection 解耦

这样推进的好处是：

- 当前功能不会倒退
- 新边界一旦确定，后续编码不会再围绕“附件是不是 mail 专属概念”来反复摇摆
- 后续如果要继续做 typed result bridge，这里可以直接复用

## 13. 一句话结论

在当前仓库里，最稳的本地文件交付方案不是“让 agent 直接发邮件附件”，而是：

**先把文件定义成 run artifact，再让 mail 成为其中一个 delivery adapter。**

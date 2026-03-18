# Artifact Markdown Rendering Plan

> 文档层级：Layer 2（当前仓库改造计划）
>
> 适用范围：`mail_based_task_manager` 当前仓库本体。
> 本文定义的是未来拟落地的 Markdown-first artifact 渲染方案，不改变当前 Layer 1 中“邮件 HTML inline preview 已实现”的事实。

## 0. 当前背景

- 日期：2026-03-15
- 相关当前协议：`docs/current/multimedia_mail_protocol.md`
- 相关计划文档：`docs/plans/run_artifact_delivery_plan.md`
- 当前进展：
  - reporter 的 Markdown-first authoring path 已进入主链
  - status mail 仍然以 plain text + HTML 形式发送
  - `artifact://<artifact_id>` 当前已用于内部 mail projection，而不是外部公开链接
- 当前事实：
  - 当前 status mail 支持 `text/plain` + `text/html`
  - 当前图片 inline preview 通过邮件 HTML 和 CID 相关 MIME part 实现
  - 当前 `artifact_resolver` 解析结果仍然偏向 `OutgoingAttachment` 邮件语义

当前实现可用，但如果最终目标是“使用 Markdown 作为统一渲染入口”，那就不应把 HTML CID 机制继续当成上层协议。

## 1. 文档目的

本文回答两个问题：

1. inline 图片要不要纳入当前这轮 artifact 方案
2. 如果最终以 Markdown 为统一渲染入口，当前仓库应该如何分层

结论是：

**要在这一阶段把 inline 图片一起纳入，但纳入的是“渲染意图”和“artifact 引用协议”，不是 mail-specific HTML 机制本身。**

## 2. 设计目标

本方案的目标是：

1. 让 Markdown 成为 status/body 渲染的 canonical 上层表示。
2. 让 inline 图片成为 artifact 元数据的一部分，而不是邮件专属特性。
3. 让 mail、本地 UI、未来其他渠道都能消费同一份 artifact 渲染意图。
4. 保持对当前 mail HTML inline preview 的兼容演进，而不是一次性推翻。

## 3. 非目标

本方案不做以下事情：

1. 不要求标准 Markdown 直接理解本地绝对路径或邮件 CID。
2. 不要求 backend 直接输出 HTML。
3. 不在这一轮引入远程 URL、对象存储或 Web 服务。
4. 不把本地渲染需求写进当前 Layer 1 协议，除非代码已经落地。

## 4. 核心判断

如果最终目标是 Markdown 渲染，那么最上层应统一成：

- canonical body: Markdown
- canonical media reference: logical artifact reference
- channel-specific rendering: adapter projection

也就是说：

1. backend / reporter 不应直接生产 mail HTML inline 语义
2. `artifact_index.json` 不应包含 `cid:`、HTML 片段等 mail 专属字段
3. mail renderer 才负责把 Markdown + artifact 引用投影成 HTML + CID

## 5. 分层建议

建议把未来渲染层拆成四层：

### 5.1 Run Artifact Truth Layer

真相源仍然是 run artifact 和 `artifact_index.json`。

这一层只描述：

- 有哪些文件
- 哪些文件是图片
- 哪些文件适合 inline preview
- 这些文件的人类可读标题/说明是什么

这一层不描述：

- HTML 结构
- CID 值
- Markdown 片段
- 邮件 MIME 细节

### 5.2 Canonical Body Layer

这一层的标准表示推荐是 Markdown。

例如状态正文最终由 reporter 生成：

```md
## Artifacts

- [final_report.md](artifact://artifact-report): Final report

- [result_chart.png](artifact://artifact-chart): Result chart (inline preview)
![Result chart](artifact://artifact-chart)
```

这里的重点不是 Markdown 语法本身，而是：

- 链接目标使用逻辑 artifact 引用
- Markdown 不直接绑定本地绝对路径
- Markdown 不直接绑定 `cid:` 或 HTML
- 当前展示结构先冻结为单一 `Artifacts` section，不拆分 `Images` / `Files`
- `Attachment Notices` 作为独立 section 固定排在 `Artifacts` 之后，不与 artifact 条目混排

### 5.3 Channel Projection Layer

这一层按渠道转换：

- mail renderer：Markdown + artifact refs -> HTML + CID + MIME attachments
- local renderer：Markdown + artifact refs -> 本地路径 / 本地预览引用
- future renderer：Markdown + artifact refs -> 其他目标格式

这一层才允许出现 mail-specific 或 viewer-specific 技术细节。

### 5.4 Delivery Layer

最后一层才是真正发送/展示：

- SMTP + MIME
- terminal/local preview
- 未来 UI 面板

## 6. `artifact_index.json` 建议字段

为了支持 Markdown-first 渲染，建议 `artifact_index.json` 从一开始就预留下列字段：

```json
{
  "version": 1,
  "task_id": "task_001",
  "artifacts_root": "runs/task_001/artifacts",
  "items": [
    {
      "artifact_id": "artifact-chart",
      "path": "E:\\repo\\tasks\\thread_001\\runs\\task_001\\artifacts\\chart.png",
      "name": "chart.png",
      "kind": "image",
      "content_type": "image/png",
      "source": "directory_fallback",
      "attach": true,
      "inline_preview": true,
      "caption": "Result chart"
    }
  ],
  "skipped": []
}
```

建议字段说明如下：

- `artifact_id`
  - 稳定逻辑引用 ID
  - 用于 Markdown `artifact://...` 引用

- `kind`
  - 推荐枚举：`image`、`file`
  - 未来可再扩成 `audio`、`video`、`pdf`

- `inline_preview`
  - 表示“推荐以内联预览方式渲染”
  - 不表示“邮件里必须用 CID”

- `caption`
  - 渲染层使用的人类可读说明

这几个字段都属于“渲染意图”，不是“邮件传输细节”。

## 7. Markdown 引用协议建议

推荐引入逻辑引用协议：

```text
artifact://<artifact_id>
```

示例：

```md
- [final_report.md](artifact://artifact-report): Final report

![Result chart](artifact://artifact-chart)
```

这样做比直接写本地绝对路径更稳，原因有三点：

1. 本地绝对路径不是稳定的跨渠道表示。
2. 标准 Markdown 本身不理解 mail CID 和本地安全策略。
3. 逻辑引用可以在不同 renderer 中投影到不同目标。

### 7.1 对图片的约定

如果 artifact 满足以下条件：

- `kind == "image"`
- `inline_preview == true`

则 Markdown 渲染器应优先使用图片语法：

```md
- [result_chart.png](artifact://artifact-chart): Result chart (inline preview)
![Caption](artifact://artifact-chart)
```

### 7.2 对普通文件的约定

非图片或不适合 inline preview 的 artifact，优先使用链接语法：

```md
- [final_report.md](artifact://artifact-report): Final report
```

### 7.3 不推荐的做法

不推荐：

- `![img](E:\absolute\path\chart.png)`
- `![img](cid:mail-runner-inline-1)`
- 让 backend 自己拼 HTML `<img>`

这些都把上层内容直接绑到了某个渠道细节。

## 8. Mail Renderer 方案

mail renderer 的职责建议如下：

1. 读取 canonical Markdown body
2. 解析其中的 `artifact://<artifact_id>` 引用
3. 从 `artifact_index.json` 找到对应 artifact
4. 若为图片且 `inline_preview == true`
   - 生成 CID
   - 把 Markdown 图片引用投影成 HTML `<img src="cid:...">`
   - 同时保留附件发送
5. 若为普通文件
   - 在 HTML 中渲染为文本链接或附件说明
   - 在 MIME 中附带附件

这意味着：

- `cid` 只在 mail renderer 中生成
- `OutgoingAttachment.content_id` 只在 mail projection 阶段赋值
- `artifact_index.json` 不需要提前知道 mail 的 content id

## 9. Local Renderer 方案

如果未来需要本地 UI / 查看器，可采用同一份 Markdown body：

1. 读取 canonical Markdown
2. 解析 `artifact://<artifact_id>`
3. 映射到本地文件路径或本地 preview handler

这保证：

- mail 和 local renderer 不会分叉成两套正文生成逻辑
- artifact 真相层只维护一份

## 10. Reporter 与模块边界建议

### `mail_runner/reporter.py`

建议后续职责：

- 先生成 canonical Markdown body
- 再提供 mail projection 所需的渲染输入
- 不直接把 HTML inline 预览机制当成唯一正文真相源

### `mail_runner/artifact_resolver.py`

建议后续职责：

- 产出 `RunArtifact`
- 写 `artifact_index.json`
- 不直接生成 mail-specific 内容

### `mail_runner/mail_io.py`

建议后续职责：

- 只负责 MIME 发送
- 接受 mail projection 后的 `OutgoingAttachment`
- 不负责决定正文里应该出现哪些 artifact

### `mail_runner/app.py`

建议后续职责：

- 在发送 status mail 前组织：
  - artifact resolve
  - artifact index persist
  - Markdown body render
  - mail projection

## 11. 与当前协议的关系

需要明确：

- 当前 Layer 1 协议仍然是“图片可作为 HTML inline preview 发送”
- 这是当前事实，不应被文档工程否定
- 但未来若要演进到 Markdown-first，Layer 2 应明确 mail HTML 只是 projection

因此，这条线的正确关系是：

- Layer 1：描述当前 HTML inline preview 行为
- Layer 2：规划未来 Markdown-first 演进路径

## 12. 建议实施顺序

推荐顺序如下：

1. 先冻结 `artifact_index.json` 的字段，至少加上 `artifact_id`、`kind`、`inline_preview`、`caption`
2. 再冻结 Markdown 引用协议 `artifact://<artifact_id>`
3. 然后让 reporter 从“直接产 HTML”过渡到“先产 Markdown，再按渠道投影”
4. 最后才重构 mail renderer 的 HTML/CID 生成路径

这个顺序可以最大限度减少返工，因为：

- artifact ID 和引用协议一旦定下来，后续 renderer 可以逐步替换
- 当前 mail HTML 能力可以继续工作
- 新旧路径能有一段并存期

## 13. 验收标准

当这一方案进入编码阶段后，第一版至少应满足：

1. `artifact_index.json` 能稳定提供 Markdown 渲染所需最小字段。
2. Markdown body 可以通过 `artifact://<artifact_id>` 引用图片和文件。
3. mail renderer 能把 Markdown 图片引用投影成 HTML inline preview。
4. inline 图片仍然保留普通附件发送行为。
5. local/non-mail renderer 后续可以复用同一份 Markdown body 和 artifact index。

## 14. 一句话结论

如果最终目标是 Markdown 渲染，那么 inline 图片现在就应纳入方案，但应被定义为：

**artifact 的渲染意图，而不是 mail HTML 的专属机制。**

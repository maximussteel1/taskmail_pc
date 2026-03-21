# Next Development Plan

## Status

- Date: 2026-03-19
- Scope: canonical next-phase development plan for `mail_based_task_manager`
- Source of truth: `docs/current/*`, `docs/plans/p9_html_mail_projection_plan.md`, `docs/plans/pc_outbound_layering_refactor_plan.md`, `docs/plans/android_consumer_contract_alignment_plan.md`, `docs/plans/android_consumer_protocol_freeze_note.md`, `docs/plans/outbound_mail_baseline_delta_checklist.md`, `docs/plans/outbound_mail_contract_convergence_plan.md`, `state.md`
- Implementation progress:
  - P1 (`Codex SDK` continuous sessions) is landed in code.
  - P2 (`active <= 4` with oldest-session auto-end) is landed in code.
  - P3 (SDK path minimal PC observability + append-only follow window) is landed in code.
  - P4 (thread lifecycle axis with explicit `/end`) is landed in code.
  - P5 (health status plus stale/stuck/orphaned visibility) is landed in code.
  - P6 (split live-mail retention for progress/action-required/receipt mail) is landed in code.
  - `P7` is complete in code, tests, and fixed live acceptance evidence.
  - `P8` first-round same-workspace explicit session targeting / routing UX is landed in code; cross-workspace routing and non-reply reuse remain open. Detailed execution plan: `docs/plans/p8_session_targeting_plan.md`.
  - `P9` repo-side HTML projection work is partially landed in code and tests. The remaining Android/client acceptance work, plus any further P9 HTML-scope expansion, is temporarily frozen. Detailed status lives in `docs/plans/p9_html_mail_projection_plan.md`.
  - The Android/Thunderbird-facing outbound consumer contract remains frozen, but `P9` itself is not the current active implementation slice while that freeze is in effect.
  - The current active repo-side next step for this area is `docs/plans/pc_outbound_layering_refactor_plan.md`: split the outbound path into render / packet / transport layers without changing the Android-facing contract.
  - Broader renderer and outbound-model convergence remains staged later in `docs/plans/outbound_mail_contract_convergence_plan.md`.
- Merged inputs:
  - `docs/archive/thread_management_user_rules_legacy.md`
  - `docs/archive/thread_management_state_model_plan_legacy.md`

## Goal

本文件取代旧的分散式 backlog 口径，作为当前仓库**唯一的下一阶段开发顺序**。

在现有 P1-P8 已落地后，当前关于 outbound/transport 这条线的口径调整为：

1. 保留已经落地的 P9 仓库侧实现与回归测试。
2. 将 P9 剩余工作明确标记为暂时冻结，不继续扩写 HTML 范围。
3. 当前 active 实施线切到 `docs/plans/pc_outbound_layering_refactor_plan.md`，先做仓库内 render / packet / transport 分层。
4. Android 与 PC 两个项目继续并行推进，但不修改当前通讯协议。
5. 如需重启 P9，先补 Android/Thunderbird 验收与 closeout，再决定是否继续。
6. neutral outbound model、summary-first plain text、subject-shape cutover 仍保持后置，不因 P9 冻结而自动提前启动。

## Current Outbound Sequencing

- Current consumer-facing authority lives in `docs/current/pc_mail_output_protocol.md` and `docs/current/multimedia_mail_protocol.md`.
- `docs/plans/android_consumer_contract_alignment_plan.md` and `docs/plans/android_consumer_protocol_freeze_note.md` define the immediate sequencing and the contract details that should stay stable while Android lands its controlled rich-text reader.
- `docs/plans/p9_html_mail_projection_plan.md` records the partially landed repo-side slice, but P9 is currently frozen and should not be treated as the active implementation queue.
- `docs/plans/pc_outbound_layering_refactor_plan.md` is the active repository-side implementation plan while P9 remains frozen; it changes internal PC boundaries only and leaves the Android-facing contract untouched.
- `docs/plans/outbound_mail_contract_convergence_plan.md` remains the post-P9 path for neutral outbound models, summary-first plain text, fragment-wrapper cleanup, and subject-shape compatibility work; it is not auto-promoted just because P9 is frozen.
- Subject-shape cutover, internal `TaskRunPacket` / neutral-model shape, and other broader convergence items remain explicitly off the current path.

## Constraints

后续开发必须遵守以下约束：

1. 不影响当前已经落地的工作进程、reply 语义和现有 mailbox loop。
2. `text/plain` 继续作为 reply/parsing truth source，HTML 只做 reading projection。
3. 不把 mail-specific 字段塞回 `artifact_index.json` 或其他 artifact truth layer。
4. P9 当前处于冻结状态；如后续重启，必须继续按已冻结的 consumer-facing contract 实施，不把 broader convergence 抢到前面。
5. subject-shape cutover、neutral outbound model、summary-first plain text 继续后置到 broader convergence。
6. 当前分步实现只做仓库内重构，不改 Android 通讯协议；两个项目可以并行推进。

## Agreed Decisions

当前已经收口的产品/协议决策如下：

- 同一件事：继续同一线程。
- 不同的事：新开线程，即使工作目录相同。
- `DONE` 只表示上一轮成功完成，不表示线程自动结束。
- `[DONE]` 是用户已收到结果的明确回执，应保留，不应被后续状态邮件删除。
- 线程需要支持用户主动结束；之后再次继续仍算恢复原线程。
- 第一轮 SDK 连续会话落地后，系统强制最多只保留 `4` 个 `active` 会话。
- 超过 `4` 个时，直接自动结束“上次活跃时间最早”的那个 `active` 会话，不做复杂交互。
- SDK 连续会话优先于后续 thread lifecycle 细化。
- P1 主线固定为 TypeScript `@openai/codex-sdk` sidecar，由 Python 主程序调用；不直接把 Python Agents SDK 作为主实现路径。
- Python 直连 `codex mcp-server` 保留为备选/参考实现，不作为第一轮主线。
- 如果采用 SDK / app-server 程序化接入，PC 端不会天然获得官方 Codex CLI/TUI；需要仓库自己提供本地可视化或观察入口。

## Priority Order

## P0：文档工程收口

### Intent

先消除文档层的多口径与过期计划，再进入下一轮实现。

### Deliverables

- 合并零散线程管理草案到单一计划文档
- 归档已被合并的临时草案
- 更新 `docs/plans/README.md` 与根 `README.md` 索引
- 确保 `docs/plans/` 只有一份明确的下一阶段优先级口径

### Done When

- `docs/plans/coding_backlog.md` 成为唯一的 next-phase canonical plan
- 旧的临时 thread-management 草案移入 `docs/archive/`
- `docs/plans/README.md` 与根 `README.md` 不再引用已过期草案

## P1：Codex SDK 连续会话接入

### Why First

- 当前最大的真实问题是 continuation/resume 丢上下文
- 这会直接伤害开发可用性
- 如果先做 thread lifecycle，而连续会话仍然脆弱，协议层会先建立在不稳的执行语义上

### Goal

在**不打断现有 CLI 工作流**的前提下，新增一条基于 TypeScript `@openai/codex-sdk` sidecar 的 Codex 连续会话路径。

详细实施方案见 `docs/plans/codex_sdk_continuous_session_plan.md`。

### Minimum Deliverable

- 新增 `CodexSdkAdapter`
- 新增一个最小 Node/TypeScript sidecar，用来承接 `@openai/codex-sdk`
- 能创建真正连续的 SDK thread/session
- 能在后续 turn 上继续同一 SDK thread/session
- 持久化 SDK 侧 thread/session id
- 与现有 `ThreadState` / `SessionState` 串起来
- 当前 CLI `CodexAdapter` 仍可保留并运行

### Guardrails

- 第一轮不强制替换现有 `codex exec` 路径
- 第一轮优先让 SDK 路径可选、可切换、可回退
- 第一轮不承诺完整重写所有 adapter 共性
- 第一轮不引入需要额外 OpenAI API token 的 Python Agents SDK orchestration 主路径

### Suggested Implementation Shape

- 在现有 adapter 层并行增加 SDK backend path
- SDK backend path 首轮采用 “Python 主程序 -> Node/TypeScript sidecar -> `@openai/codex-sdk`” 结构
- 为 thread/session 持久化新增 SDK 连续会话标识字段
- 对 Codex 先做，OpenCode 不在这一轮并行拉齐
- Python 直连 `codex mcp-server` 仅保留为备选，不作为首轮主实现
- 当前 CLI 路径继续作为 fallback 与对照实现

### Done When

- 同一线程的多轮继续不再依赖 `codex exec resume`
- 本地状态可明确指向同一个 SDK 连续会话
- 至少有覆盖“新建 -> 连续两轮继续 -> DONE”的自动化测试

## P2：强制 `active <= 4`

### Why Second

- 一旦有了真正连续会话，线程数会自然累积
- 这是自用系统，不需要复杂工作集管理策略

### Rule

系统最多只允许 `4` 个 `active` 会话。

当某个动作会导致 `active` 会话数超过 `4` 时：

- 自动结束 `last_active_at` 最早的那个 `active` 会话
- 当前用户正在启动或恢复的目标会话不应被自己淘汰

### First-Round Simplicity Rule

第一轮允许使用简单规则：

- 如无独立 `last_active_at` 字段，先用 thread/session 的 `updated_at` 作为近似活跃时间
- 不做复杂提示流
- 不做交互式确认

### Applies To

- 新首封任务
- `/new`
- 将 `ended` 会话恢复为 `active`

### Done When

- active 工作集不会无界增长
- 超限时系统行为固定且可预测
- 有覆盖“第 5 个 active 进入时自动结束最旧 active”的测试

## P3：SDK 路径的最小 PC 可视化

Detailed implementation plan: `docs/plans/p3_streaming_session_window_plan.md`.

### Why Third

- SDK 路径不会自动给出官方 Codex CLI/TUI 体验
- 如果没有本地可视化，连续会话虽然可用，但 PC 端排障会变难

### Goal

在不做完整桌面 UI 的前提下，提供最小可视化与观察面。

### Minimum Deliverable

- 扩展现有 `mail_runner.observe`
- 能看到：
  - 哪些线程正在运行
  - 当前走的是 CLI 还是 SDK 路径
  - 最近一次事件时间
  - 最近摘要
  - 当前 SDK 连续会话标识

### Important Product Note

这一层不是“显示官方 Codex CLI”，而是仓库自己的观察面。

### Done When

- 用户在 PC 端能判断 SDK 会话是否仍在推进
- 后续做 stuck/orphaned 判断时已有可复用字段

## P4：线程生命周期轴

### Goal

把“上一轮运行结果”和“线程是否仍属于活跃工作集”拆开。

### First Target

- 新增 lifecycle：`active` / `ended`
- 新增 `/end`
- `/resume` 可将 `ended` 线程恢复为 `active`

### Notes

- `done` / `failed` / `killed` 不自动等于 `ended`
- lifecycle 进入代码前，P1 和 P2 可以先用轻量字段或近似规则过渡

### Done When

- 用户可主动结束线程
- ended 线程默认退出主工作集
- 恢复线程不需要新开一条 thread

## P5：线程健康状态与卡死判断

Detailed implementation plan: `docs/plans/p5_p6_health_and_mail_retention_plan.md`.

### Goal

让用户能判断“这个线程是不是不工作了”。

### First Target

- 健康状态至少支持：
  - `normal`
  - `stale`
  - `suspected_stuck`
  - `orphaned`
- 增加最小心跳/最近进展字段

### Done When

- 用户能看见哪些线程只是长时间未更新，哪些线程疑似卡死
- `/kill` 和手动恢复路径有清晰判断依据

## P6：邮件保留语义收口

Detailed implementation plan: `docs/plans/p5_p6_health_and_mail_retention_plan.md`.

### Goal

把 thread 的真实状态与 live mailbox 的保留语义拆开。

### First Target

- `[DONE]` 不再属于可删状态
- `[FAILED]`、`[KILLED]` 作为回执类邮件一并评估保留
- 只让纯进度类邮件继续走 replacement 语义

### Suggested Classification

- `progress`：
  - `[ACCEPTED]`
  - `[RUNNING]`
  - `[STATUS]`
- `action_required`：
  - `[QUESTION]`
  - `[PAUSED]`
- `receipt`：
  - `[DONE]`
  - `[FAILED]`
  - `[KILLED]`

### Done When

- 用户在邮箱中能稳定看到 `[DONE]` 结果回执
- live mailbox 清理逻辑不再误删里程碑回执

## P7：运行时硬化与结果真相层收口

详细执行方案见 `docs/plans/p7_acceptance_and_structured_output_plan.md`。

### P7A：Fixed Real-Mailbox Acceptance

状态：已完成，最新证据见 `._tmp_live_mail_question_smoke\opencode-question-20260318_133540-07ef74\result.json` 与 `._tmp_live_mail_kill_smoke\codex-kill-20260318_133818-b4d2a5\result.json`

先把下面两条升级为固定 acceptance：

- 真实 backend 的 `[QUESTION] -> ANSWER -> DONE`
- 正常 mailbox loop 下的真实 backend `KILL`

### P7B：CLI Structured Output Parsing

状态：已完成。adapter 现已通过 structured run-result capsule 回填 `changed_files`、`tests_passed`、`error_type`、`error_message`，并保持旧的 summary fallback。

在 adapter 层补结构化结果解析，优先填充：

- `changed_files`
- `tests_passed`
- `error_type`
- `error_message`

并保持当前启发式 summary fallback。

### 明确不并入 P7 的事项

- 更完整的 thread/session 管理入口，例如显式 session targeting UX
- artifact index + Markdown 渲染进一步收口
- 其余 adapter 通道的长期统一

## P9: HTML Mail Projection For Thunderbird Reading

Detailed execution plan: `docs/plans/p9_html_mail_projection_plan.md`.

Related contract inputs:

- `docs/current/pc_mail_output_protocol.md`
- `docs/current/multimedia_mail_protocol.md`
- `docs/plans/android_consumer_contract_alignment_plan.md`
- `docs/plans/android_consumer_protocol_freeze_note.md`

### Goal

Keep Markdown as the canonical authored body and keep `text/plain` as the reply/parsing truth source, while improving outbound `text/html` so Thunderbird/mobile clients can use it as the primary reading surface.

### Scope

- Improve status-mail HTML readability for Thunderbird/mobile clients.
- Continue sending `multipart/alternative` with both `text/plain` and `text/html`.
- Reuse the existing Markdown-to-HTML projection plus CID inline image path.
- Keep artifact truth and `artifact_index.json` unchanged.
- Treat the already frozen Android/Thunderbird consumer contract as input, not as open redesign scope.

### Explicitly Not In P9

- Do not make `text/html` the only truth source.
- Do not change Android reply parsing to depend on HTML.
- Do not push mail-specific fields such as `cid:` into `artifact_index.json`.
- Do not expand P9 into a larger cross-channel renderer rewrite.
- Do not pull summary-first plain text or neutral outbound-model convergence into P9.

## Explicitly Deprioritized

以下事项当前不应插队到 P9 之前：

- 先做 neutral outbound model 或 `TaskRunPacket` 形状收敛
- 先做 summary-first plain text 改写
- 先做 subject-shape cutover
- 先做完整桌面 UI 或平台级 memory / tool registry 对接
- 先把 cross-workspace routing、non-reply reuse policy 等更宽的线程 UX 问题并入当前 outbound workstream

## Canonical Inputs Before Coding

开始下一轮编码前，优先阅读：

1. `docs/current/mail_protocol.md`
2. `docs/current/multimedia_mail_protocol.md`
3. `docs/current/pc_mail_output_protocol.md`
4. `docs/current/session_scheduler_status.md`
5. `docs/plans/coding_backlog.md`
6. `docs/plans/p9_html_mail_projection_plan.md`
7. `docs/plans/android_consumer_contract_alignment_plan.md`
8. `docs/plans/android_consumer_protocol_freeze_note.md`
9. `docs/plans/outbound_mail_baseline_delta_checklist.md`
10. `docs/plans/outbound_mail_contract_convergence_plan.md`
11. `state.md`

## Coding Guardrails

编码阶段仍应遵守：

1. 不直接破坏当前 CLI 工作进程。
2. SDK 接入优先做 additive path，不先做 destructive replacement。
3. 不引入按标题自动复用 session 的隐式猜测。
4. 状态模型演进应先改文档，再改代码。
5. 每一轮实现后同步更新 `README.md`、`state.md` 和相关 canonical 文档。

## Definition Of Done

若要认定“下一阶段计划已收口”，至少应满足：

- 只剩一份 canonical next-phase plan：本文件
- 文档索引与优先级口径一致
- outbound consumer contract freeze、P9 按当前仓库侧进度冻结、PC outbound layering refactor 作为 active 实施线 这三条当前核心顺序已经被明确固定
- neutral outbound model、summary-first plain text、subject-shape cutover 已被明确后置到 broader convergence 计划
- thread-management 临时草案已归档，不再与主计划并列

# 文档分层方案

## 状态

- 日期：2026-03-15
- 适用范围：`mail_based_task_manager` 当前仓库，以及与之相关的外部 Task Manager 平台文档
- 目标：把“当前仓库文档”和“未来平台文档”分层，避免二者继续混写、互相越位
- Current Progress: `docs/current/`、`docs/plans/`、`docs/platform/`、`docs/research/`、`docs/archive/` 已建立，当前协议文档、平台文档和 legacy 文档已完成首轮归位

## 1. 核心判断

当前仓库的正确定位不是“完整 Task Manager 平台”，而是：

**一个已经具备执行、恢复、调度能力的邮件控制面 / 邮件适配层。**

因此，文档体系必须明确区分两类内容：

1. 当前仓库已经实现或即将实现的内容
2. 未来 Task Manager 平台的上位架构、接口和路线图

如果不先分层，后续会持续出现以下问题：

- README、设计文档、外部规划文档分别描述不同系统层级
- 某些“待做事项”其实已经落地，但仍被写成未来计划
- 平台级接口规范会被误解为当前仓库已经承担的职责
- 后续 Android / PC / 平台桥接工作缺少稳定的文档边界

## 2. 分层原则

文档分层遵循以下原则：

1. 先区分“当前事实”和“未来规划”。
2. 先区分“仓库内实现边界”和“平台上位边界”。
3. 协议、实现、路线图、研究材料不能混在同一层。
4. 每个主题只保留一个首要真相源。
5. 历史输入文档可以保留，但不再承担当前事实定义职责。

## 3. 文档层级定义

建议把文档分为五层。

### Layer 0：入口与当前事实

这一层回答“项目现在是什么、已经做到哪里、如何运行”。

用途：

- 面向当前仓库使用者
- 作为项目入口、状态总览和验证快照的聚合层
- 用于快速确认现状，而不是展开大篇幅设计推导

推荐文件：

- `README.md`
- `state.md`

规则：

- `README.md` 是当前项目入口和能力总览
- `state.md` 是阶段状态、验证结果、已知缺口的运行态快照
- 这一层只写“当前真相”，不承载远期平台愿景
- 如与 `docs/current/*` 冲突，以 `docs/current/*` 为准；`README.md` 与 `state.md` 不重新定义协议细节

### Layer 1：当前仓库的实现与协议文档

这一层回答“当前仓库内部具体怎么工作，哪些协议是当前正式能力”。

用途：

- 描述 mail 协议、问答协议、附件协议、调度行为
- 为当前代码和测试提供设计真相源
- 与仓库代码保持同频演进

当前 canonical 文件：

- `docs/current/mail_protocol.md`
- `docs/current/multi_question_protocol.md`
- `docs/current/multimedia_mail_protocol.md`
- `docs/current/session_scheduler_status.md`

已归档的旧来源文件：

- `docs/archive/multi_question_protocol_implementation_legacy.md`
- `docs/archive/multimedia_mail_design_legacy.md`
- `docs/archive/session_scheduler_plan_legacy.md`

本轮已建立的稳定路径：

- `docs/current/mail_protocol.md`
- `docs/current/multi_question_protocol.md`
- `docs/current/multimedia_mail_protocol.md`
- `docs/current/session_scheduler_status.md`

规则：

- 本层是当前协议和运行时行为的首要真相源
- 本层文档只描述当前仓库边界内的行为
- 如果某项能力已经在代码中落地，本层文档必须优先更新
- 本层优先级高于“改造计划”和“平台路线图”

### Layer 2：当前仓库的演进与改造计划

这一层回答“这个仓库接下来怎么继续整理和收口”。

用途：

- 只针对 `mail_based_task_manager` 本体
- 规划当前仓库下一阶段的重构、协议冻结和补口顺序
- 不越位定义完整平台实现

建议归入本层的文件：

- `docs/plans/README.md`
- `docs/plans/coding_backlog.md`
- `docs/plans/mail_adapter_refactor_plan.md`

当前主要位置：

- `docs/plans/README.md`
- `docs/plans/coding_backlog.md`
- `docs/plans/mail_adapter_refactor_plan.md`

规则：

- 本层默认以当前仓库为唯一对象
- 本层可以引用平台方向，但不能把平台未实现能力写成当前仓库职责
- 本层优先级低于 Layer 0 和 Layer 1，高于平台文档

### Layer 3：未来 Task Manager 平台文档

这一层回答“未来平台整体要长成什么样，当前仓库如何挂接进去”。

用途：

- 作为上位架构、首期接口和实施路线的基线
- 为未来独立平台或上层调度系统提供设计约束
- 明确当前仓库在平台中的角色是 mail adapter，而不是平台本体

建议归入本层的文件：

- `docs/platform/README.md`
- `docs/platform/task_manager_platform_design.md`
- `docs/platform/task_manager_platform_delivery.md`
- `docs/platform/task_manager_implementation_roadmap.md`
- `docs/platform/task_manager_tool_interface_spec.md`
- `docs/platform/schemas/task_manager_schemas_v0.1.zip`

当前主要位置：

- `docs/platform/README.md`
- `docs/platform/task_manager_platform_design.md`
- `docs/platform/task_manager_platform_delivery.md`
- `docs/platform/task_manager_implementation_roadmap.md`
- `docs/platform/task_manager_tool_interface_spec.md`
- `docs/platform/schemas/task_manager_schemas_v0.1.zip`

规则：

- 本层不直接定义当前仓库的已实现行为
- 本层只定义上位边界、平台接口、工具规范和未来路线
- 当前仓库如果要接入本层协议，必须通过 Layer 2 的桥接计划来落地

### Layer 4：研究输入与历史归档

这一层回答“项目最初是怎么提出的、做过哪些阶段研究、哪些旧文档已不再是当前真相源”。

用途：

- 保存最初需求、阶段研究和过期版本
- 供回溯决策背景使用
- 不直接作为当前行为判定依据

当前应归入本层的文件：

- `task.md`
- `docs/research/taskmail_phase0_research.md`

建议扩展目录：

- `docs/research/`
- `docs/archive/`

规则：

- 本层文档默认不作为“当前实现是否正确”的裁决依据
- 如与 Layer 0 或 Layer 1 冲突，以更贴近当前代码与协议的文档为准；涉及当前行为 / 协议时优先看 Layer 1，涉及项目总览 / 验证快照时看 Layer 0

## 4. 文档冲突时的优先级

建议明确如下优先级：

1. Layer 1：当前仓库实现与协议
2. Layer 0：入口与当前事实
3. Layer 2：当前仓库演进计划
4. Layer 3：未来平台文档
5. Layer 4：研究输入与历史归档

解释：

- “当前已经怎么做”优先于“原本打算怎么做”
- “当前仓库边界”优先于“未来平台边界”
- “已验证状态”优先于“早期需求文本”

## 5. 推荐目录结构

推荐的目标结构如下：

```text
README.md
state.md
task.md
docs/
  current/
    README.md
    mail_protocol.md
    android_reply_method_rules.md
    task_view_mail_parsing_rules.md
    multi_question_protocol.md
    multimedia_mail_protocol.md
    session_scheduler_status.md
  plans/
    README.md
    coding_backlog.md
    mail_adapter_refactor_plan.md
  platform/
    README.md
    task_manager_platform_design.md
    task_manager_platform_delivery.md
    task_manager_implementation_roadmap.md
    task_manager_tool_interface_spec.md
    schemas/
      task_manager_schemas_v0.1.zip
  research/
    README.md
    taskmail_phase0_research.md
  archive/
    README.md
    ...
```

说明：

- 本轮可以先新增目录与索引，不必立即移动所有文件
- 如果后续决定把平台文档独立到单独仓库，本层结构仍然成立，只是 `docs/platform/` 可以整体迁出

## 6. 现有文档到目标层的映射

| 当前文件 | 建议层级 | 角色说明 | 推荐后续动作 |
| --- | --- | --- | --- |
| `README.md` | Layer 0 | 项目入口与当前能力总览 | 保持精简，持续更新当前事实 |
| `state.md` | Layer 0 | 阶段状态与验证快照 | 每次关键行为变化后同步更新 |
| `task.md` | Layer 4 | 原始需求输入 | 保留，不再作为当前实现真相源 |
| `docs/current/mail_protocol.md` | Layer 1 | 当前邮件控制面协议索引 | 作为当前 mail protocol 入口 |
| `docs/current/multi_question_protocol.md` | Layer 1 | 当前多问题协议 | 作为当前 canonical 文档 |
| `docs/current/multimedia_mail_protocol.md` | Layer 1 | 当前附件与制品协议 | 作为当前 canonical 文档 |
| `docs/current/session_scheduler_status.md` | Layer 1 | 当前调度状态与剩余缺口 | 作为当前 canonical 文档 |
| `docs/archive/multi_question_protocol_implementation_legacy.md` | Layer 4 | Layer 1 文档的旧来源文件 | 已归档 |
| `docs/archive/multimedia_mail_design_legacy.md` | Layer 4 | Layer 1 文档的旧来源文件 | 已归档 |
| `docs/archive/session_scheduler_plan_legacy.md` | Layer 4 | Layer 1 文档的旧来源文件 | 已归档 |
| `docs/archive/taskmail_phase0_research_legacy.md` | Layer 4 | 研究文档旧来源文件 | 已归档 |
| `docs/research/taskmail_phase0_research.md` | Layer 4 | 早期研究材料 | 作为研究材料保留 |
| `docs/plans/mail_adapter_refactor_plan.md` | Layer 2 | 当前仓库改造计划 | 后续重标已落地 / 未落地项 |
| `docs/platform/task_manager_platform_design.md` | Layer 3 | 平台上位设计 | 作为未来平台基线 |
| `docs/platform/task_manager_platform_delivery.md` | Layer 3 | 平台首期落地方案 | 作为未来平台实施基线 |
| `docs/platform/task_manager_implementation_roadmap.md` | Layer 3 | 平台实施路线 | 作为未来平台路线图 |
| `docs/platform/task_manager_tool_interface_spec.md` | Layer 3 | 平台工具接口规范 | 作为未来平台工具契约 |
| `docs/platform/schemas/task_manager_schemas_v0.1.zip` | Layer 3 | 平台 schema 基线包 | 作为未来平台 schema 资产 |

## 7. 命名规则

建议后续文档命名遵循以下约定：

- `*_protocol.md`：当前正式协议
- `*_design.md`：较稳定的结构设计
- `*_plan.md`：实施计划、重构顺序、待完成事项
- `*_status.md`：当前状态、差距、已落地与未落地项
- `*_research.md`：探索性研究与预研材料
- `archive/`：已过时但需要保留的版本

补充规则：

- 当前仓库长期使用的活跃文档，优先不要在文件名里持续叠加 `v0.x`
- 如需要保留版本，建议在归档层保留带版本号文件
- 活跃文档尽量使用稳定文件名，让链接和引用更持久

## 8. 维护规则

建议建立以下维护纪律：

1. 行为变化先更新 Layer 1，再同步 Layer 0；涉及后续整理方向时再更新 Layer 2。
2. 平台方向变化先更新 Layer 3，不反向污染当前仓库文档。
3. 新文档在创建时必须先判断属于哪个层级。
4. 一个主题只能有一个主文档，其他文档只能引用，不重复定义。
5. 每次阶段性交付后，至少同步更新 `docs/current/*`、`README.md` 和 `state.md`。

## 9. 建议的实施顺序

建议按以下顺序推进文档治理：

### 第一步

先确认本方案作为文档分层基线。

### 第二步

把 Layer 2 改造计划统一收口到 `docs/plans/README.md`、`docs/plans/coding_backlog.md` 和专题计划文档。

### 第三步

把平台四份文档和 schema 包纳入 `docs/platform/`，并在文档首页注明：

“这些文档描述的是未来平台，不等同于当前仓库实现范围。”

### 第四步

对 Layer 1 文档做一次收口：

- 标注哪些能力已落地
- 标注哪些仍为 open issues
- 去掉已经失真的“待做项”

### 第五步

更新 `README.md` 和 `state.md`，使其与当前代码和测试结果一致。

## 10. 当前最值得立即做的三件事

1. 继续把 `docs/current/*` 维持为当前协议和运行时行为的唯一真相源。
2. 让 `README.md` 与 `state.md` 只承担入口、总览和验证快照职责，不再与协议细节分叉。
3. 清理仍引用旧示例文件名或旧优先级表述的 Layer 2 / Layer 3 文档。

## 11. 一句话结论

当前仓库的文档体系不应再围绕“是不是平台”来组织，而应围绕以下顺序来组织：

**当前事实 -> 当前协议 -> 当前改造计划 -> 未来平台 -> 历史输入。**

只有先把这五层分开，后续的 Android、PC、外部记忆系统、统一工具协议和平台桥接工作才会真正顺起来。

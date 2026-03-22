# Phase 4 Dual-Stack Parity Plan

## Status

- Date: 2026-03-22
- Scope: repository-side Phase 4 execution plan for dual-stack parity, mismatch triage, rollback triggers, and the first primary-path switch gate
- Layer: Layer 2 repository plan
- Related docs:
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/phase3_direct_inbound_closeout_handoff.md`
  - `docs/plans/phase2_direct_outbound_contract_v1.md`
  - `docs/plans/phase3_direct_inbound_mapping_v1.md`
  - `docs/plans/phase3_direct_inbound_wire_v1.md`
  - `docs/plans/phase3_direct_inbound_fixture_package_v1.md`
  - `docs/plans/phase4_dual_stack_parity_checklist.md`
  - `docs/plans/phase4_mismatch_ledger.md`
  - `docs/plans/phase4_rollback_trigger_note.md`
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-next-session-handoff-2026-03-22-phase4-dual-stack-start.md`

## Purpose

把 `android_pc_vps_coordinated_execution_plan.md` 里的 Phase 4 intent 展开成“仓库侧下一轮要按什么顺序落地”的可执行计划。

这份计划不改变当前 Layer 1 事实：

- mail fallback 继续保留
- 当前 user-visible mail contract 不变
- direct `reply` / direct `/status` 仍不在本轮默认范围内

## Current Starting Point

当前仓库侧已经具备以下 Phase 4 起点条件：

- Phase 2 direct `new_task` v1 已完成仓库侧 live closeout
- Phase 3 active-session detail sidecar v1 已冻结，且仓库侧 mapping / wire / fixture / provider 已落地
- Android 侧已在 2026-03-22 把主线切到 Phase 4 dual-stack parity
- Android 正式 flow 上当前唯一已经完成 direct closeout 的 direct business slice 仍是 `new_task`
- `new_task` 的 repo-side outcome normalization 已落地：accepted / fallback-classified rejection / hard rejection 有统一 classifier，accepted packet 后续失败也会持久化 `last_error_code`
- 与这层实现直接相关的 targeted tests 和 full suite 已通过，当前仓库没有新增 repo-side mismatch signal
- `reply`、`/status` 的单独跨仓库 contract freeze 现在可以存在于 planning 层，但它仍不改变当前 Layer 1，也不自动把
  这些 flow 纳入本轮 Phase 4 covered flow

因此，Phase 4 不应被理解为“全量 direct 化”，而应读作“先围绕已闭环 flow 建立 parity、rollback 与 primary-path switch 的可信度”。

## Phase 4 Goal

Phase 4 的仓库侧目标不是扩 scope，而是把已覆盖 flow 做到：

1. 可比较：direct path 与 mail path 的关键结果可逐项对账
2. 可诊断：发现差异时知道记到哪里、看什么证据、由谁处理
3. 可回退：mail fallback 保持可用，且 rollback trigger 明确
4. 可切换：只有在 covered flow parity 足够时，才允许 primary-path switch

## Covered Flow Definition (v1)

Phase 4 v1 先把 covered flows 收窄为：

- direct `new_task`
- 该 flow 下的三类 send-side outcome：
  - direct accepted
  - ack-level fallback-classified rejection -> mail fallback
  - ack-level hard rejection -> local stop / no implicit mail send
- 与这些 outcome 对应的 user-visible thread outcome / summary / error semantics

本轮先不把以下内容并入 covered flows：

- direct `reply`
- direct `/status`
- 全量 workspace / history read path 切换
- attachment binary sync
- 移除 mail fallback

Phase 3 active-session detail sidecar 当前可作为 parity 观察辅助输入，但不应在本计划第一轮里被提升为“必须先切 direct-default 的 primary read path”。

## Phase 4 Shared Artifacts To Produce

按 `android_pc_vps_coordinated_execution_plan.md` 的 Phase 4 定义，本轮至少应产出三份共享工件：

1. `phase4_dual_stack_parity_checklist.md`
2. `phase4_mismatch_ledger.md`
3. `phase4_rollback_trigger_note.md`

这些工件的 repository-side first skeleton 现在已经显式存在于：

- `docs/plans/phase4_dual_stack_parity_checklist.md`
- `docs/plans/phase4_mismatch_ledger.md`
- `docs/plans/phase4_rollback_trigger_note.md`

这三份工件的角色分别是：

- parity checklist：定义 covered flow 的逐项对账面
- mismatch ledger：记录当前已知差异、严重度、归属与状态
- rollback trigger note：把“只记日志 / 自动 fallback / 中止 direct-default”写成明确规则

## Work Packages

### WP1：冻结 covered-flow parity 面

目标：把 Phase 4 第一轮真正要比较的东西写死，避免 scope 漂移。

输出：

- `phase4_dual_stack_parity_checklist.md` 首版

至少应锁定以下 parity 字段：

- bootstrap / packet submission 是否成功
- `packet_ack.accepted`、fallback-classified rejection、hard rejection 的分类语义
- direct accepted 后是否按既有业务路径生成 thread / session / status mail
- mail fallback 后用户可见结果是否仍满足当前 canonical mail contract
- hard rejection 时是否保持“无隐式 mail send、错误语义明确”的现状
- summary / terminal outcome 是否与 mail-only 基线一致

约束：

- 不在本工作包里发明新的 direct action
- 不把 Android UI 细节直接混成 PC 业务合同

### WP2：补齐仓库侧 parity 证据面

目标：确认当前仓库已经能给 parity / rollback 提供足够证据；若不足，再补最小观测字段。

优先审计这些位置：

- `mail_runner/outbound/relay_bootstrap.py`
- `mail_runner/outbound/relay_transport.py`
- `mail_runner/outbound/service.py`
- `mail_runner/relay_server/`
- outbound journal / delivery attempt / packet store 对外可见字段

需要回答的问题：

- direct accepted / fallback / hard rejection 当前分别能留下哪些 machine-readable 证据
- 哪些证据已经在 packet store / journal / runtime state 中
- 哪些差异目前只能靠人工翻日志判断
- 是否需要补最小字段，才能支撑 rollback trigger 的自动或半自动判定

约束：

- 只补 parity 观测能力，不改变当前 user-visible contract
- 不把 scheduler、reply parser、P9 HTML 或 broader outbound convergence 混进来

### WP3：建立 mismatch ledger 和分级规则

目标：把“发现差异怎么办”从口头判断改成显式台账。

输出：

- `phase4_mismatch_ledger.md` 首版

建议最少包含这些列：

- mismatch_id
- covered_flow
- scenario
- observed_behavior
- expected_behavior
- severity
- owner_repo
- current_status
- evidence
- rollback_impact

建议严重度先固定为三层：

- `observe_only`
- `fallback_required`
- `switch_blocker`

### WP4：冻结 rollback trigger

目标：把“什么情况下不能再继续 direct-default”写成可信规则。

输出：

- `phase4_rollback_trigger_note.md` 首版

至少应覆盖：

- bootstrap 失败
- `packet_ack` 分类与预期不一致
- direct accepted 后没有落到预期 thread / status mail 结果
- fallback 路径可用性丢失
- user-visible summary / terminal outcome 与 mail baseline 发生高风险漂移

每条 trigger 至少应写明：

- 触发条件
- 证据来源
- 自动动作或人工动作
- 是否阻塞 primary-path switch

### WP5：做第一轮 parity 验证

目标：用现有测试和 live smoke 把 Phase 4 第一轮矩阵跑出来。

仓库侧优先验证入口：

- `tests/test_outbound_relay_bootstrap.py`
- `tests/test_outbound_relay_transport.py`
- `tests/test_outbound_service.py`
- `tests/test_relay_server_direct_actions.py`
- `tests/test_relay_server_loopback.py`
- `tests/test_relay_server_phase3_emitter.py`
- `tests/test_relay_server_phase3_subscription.py`
- `tests/test_relay_server_phase3_fixture_package.py`
- `tests/test_relay_server_runtime.py`

live / runtime 抓手：

- `scripts/live_smoke_mail_roundtrip.py`
- `scripts/live_smoke_mail_sync.py`
- `scripts/fetch_bot_mails.ps1`
- `scripts/fetch_user_mails.ps1`
- `scripts/diagnose_runtime_health.py`

执行顺序建议：

1. 先跑 targeted tests 固定 direct accepted / fallback / hard rejection 语义
2. 再跑 live smoke 补 mail delivery 与 user-visible outcome 证据
3. 最后把结果写回 parity checklist 和 mismatch ledger

### WP6：定义 primary-path switch gate

目标：只有当 parity 足够时，才允许 covered flow 进入 direct-default。

第一轮 gate 建议收窄为：

- 仅面向 `new_task`
- 仅在 parity checklist 关键项完成后开启评审
- 仅在 rollback trigger note 已明确且 mail fallback 仍通过验证时允许切换

当前不应在此 gate 中混入：

- `reply`
- `/status`
- 更广的 direct read-side 默认切换

## Validation Strategy

每个工作包结束时建议按以下顺序验证：

1. 先更新文档工件
2. 再跑与改动直接相关的 targeted tests
3. 若触及 shared runtime path，再跑 `.\.venv\Scripts\python.exe -m pytest`
4. 对需要跨 transport 证据的项，再补 live smoke

## Non-Goals

本计划当前明确不做：

- 把 direct `reply` / direct `/status` 直接写进 Phase 4 实现线
- 提前删除 mail fallback
- 把 Android UI 重构细节写成 PC 业务合同
- 把 P9 / broader outbound convergence / scheduler 重构混进当前阶段

## Exit Reading

Phase 4 在仓库侧可以读作“准备收口”至少要满足：

- `new_task` covered flow 的 parity checklist 已形成稳定口径
- mismatch ledger 中不存在未界定归属的高严重度差异
- rollback trigger note 足够明确，能支撑可信回退
- mail fallback 仍保持可用并有测试或 smoke 证据
- Android / PC 双方都同意 covered flow 的 direct-default 切换边界

## Recommended Next Session Order

如果下一次继续推进，建议按以下顺序开工：

1. 先用 Android 侧当前实现去跑 `new_task` 的三场景矩阵：direct accepted、fallback-classified rejection、hard rejection
2. 把 Android 实测结果与当前 repo-side classifier / packet-store evidence 对账，回填到共享 parity checklist
3. 如果出现已确认差异，再显式登记到 mismatch ledger；如果没有，就继续补 live smoke 的 mail outcome 证据
4. 只有在 parity checklist、mismatch ledger、rollback trigger note 三份工件都形成 cross-repo 首轮闭环后，才讨论 `new_task` 的 direct-default 开关

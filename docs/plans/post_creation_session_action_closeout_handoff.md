# Post-Creation Session-Action Closeout Handoff

## Status

- Date: 2026-03-23
- Scope: repository-side handoff from the first post-creation session-action implementation slice into live closeout evidence capture and Layer 1 upgrade review
- Layer: Layer 2 repository note
- Related docs:
  - `docs/plans/post_creation_session_action_contract_v1.md`
  - `docs/plans/post_creation_session_action_execution_plan.md`
  - `docs/plans/phase4_dual_stack_parity_plan.md`
  - `docs/plans/phase5_freeze_review_precheck.md`
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/README.md`

## Purpose

把 post-creation 这条线从“仓库内还在补实现”切换成“repo-side 第一刀已经落地，下一步该收什么证据、何时决定是否升级 Layer 1”。

这份 handoff 不重写 shared contract，也不直接改写 `docs/current/*`；它只负责把当前 closeout 快照、live 证据门槛和下一次会话的默认起点写清楚。

## Repo-Side Closeout Snapshot

截至 2026-03-23，仓库侧可以把 post-creation session-action v1 读作已经完成第一轮实现闭环：

- `post_creation_session_action_contract_v1.md` 与 `post_creation_session_action_execution_plan.md` 两份前置工件已齐备
- current-session direct `/status` accepted path 已落地
- current-session plain direct `reply` accepted path 已落地
- server-side current-session resolver、mail-bridge seam、post-creation classifier 已落地
- current-session direct `/status` accepted 后会落 thread-scoped `session_action_closeout.json`
- current-session direct `reply` accepted 后会在 run-scoped `canonical_summary.json` 中保留：
  - `action_type`
  - `target_session_identity`
  - `request_id`
- `build_taskmail_closeout_bundle.py` 已能同时解释：
  - run-scoped `canonical_summary.json`
  - thread-scoped `session_action_closeout.json`
- repository full suite 已通过：
  - `.\.venv\Scripts\python.exe -m pytest`
  - `395 passed`

当前不应把这次 closeout 误读成：

- `docs/current/mail_protocol.md` 已经授权 direct `reply` / direct `/status`
- `docs/current/android_runner_communication_contract.md` 已经允许 Android 把 post-creation control 默认接到 `/relay`
- Phase 4 covered flow 已经从 `new_task` 扩到 post-creation action
- quick answer / `Answers:` / paused `/resume` / attachment continuation 已被吸收入 v1
- `direct-default` 已经可以宣告

## Handoff Decisions

1. post-creation session-action 的第一轮仓库实现现在可以视为“代码切片已收口”。
2. 下一步不再优先扩 scope，也不再优先补协议文本；默认起点改为 live closeout evidence。
3. 第一轮 live 证据样本固定为两条：
   - current-session direct `/status`
   - current-session plain direct `reply`
4. 只有当这两条样本都能稳定解释 `same_run_bind`、canonical mail outcome 和 closeout anchors 时，才讨论是否回写 `docs/current/*`。
5. 若样本出现 drift 或 bind gap，优先按 post-creation line 单独收口，不把它隐式并进当前 Phase 4 `new_task` covered flow。

## First Live Evidence Package

### Sample A: current-session direct `/status`

必须至少收齐：

- Android retained send evidence
  - 最好保留 `request_id`
- relay `packet_ack` 与 packet store
- PC ingress raw mail
- `tasks/<thread_id>/session_actions/<request_id>/session_action_closeout.json`
- canonical `[STATUS]` mail
- 由 `build_taskmail_closeout_bundle.py` 生成的 closeout bundle

建议判读重点：

- bundle 的 canonical outcome source 应稳定落到 `session_action_closeout`
- `terminal_mail_subject` 应与当前 canonical `[STATUS]` mail 语义一致
- `action_type = status`
- `target_session_identity` 应与 resolved current session 一致
- `same_run_bind` 仍沿当前字段名保留，但这里应读作“同一 action continuity 锚点是否成立”，而不是“必须启动新 run”

### Sample B: current-session plain direct `reply`

必须至少收齐：

- Android retained send evidence
  - 最好保留 `request_id`
- relay `packet_ack` 与 packet store
- PC ingress raw mail
- `runs/<task_id>/canonical_summary.json`
- 后续 canonical status / receipt mail
- 由 `build_taskmail_closeout_bundle.py` 生成的 closeout bundle

建议判读重点：

- bundle 的 canonical outcome source 应稳定落到 `canonical_summary`
- `canonical_summary.json` 应保留：
  - `action_type = reply`
  - `target_session_identity`
  - `request_id`
  - `ingress_message_id`
- `same_run_bind` 应至少能稳定落到：
  - `request_id`
  - 或 `transport_message_id <-> ingress_message_id`
- 后续 user-visible mail 应继续沿当前 mail path 产出，而不是 direct-only outcome

### Sample Selection Advice

为了降低第一轮 live closeout 的噪声，建议：

- `/status` 选一个当前状态清楚、且 subject / state capsule 已稳定的 session
- `reply` 选一个 plain continuation 就能很快收敛的 session
- 第一轮不要故意挑：
  - multi-question `Answers:`
  - quick answer
  - paused `/resume`
  - attachment continuation

## Suggested Execution Order

1. 在 Android 侧发起一条 current-session direct `/status`
2. 立即收集 Android send evidence、relay ack、PC ingress raw mail 与 terminal `[STATUS]` mail
3. 生成 `/status` 的 closeout bundle
4. 再发起一条 current-session plain direct `reply`
5. 等待其收敛到当前 canonical mail outcome
6. 收集 `canonical_summary.json`、terminal mail 与 bundle
7. 对两条样本分别做 bind / semantics review

建议使用的仓库内入口：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_taskmail_closeout_bundle.py <thread_id> --task-root .\tasks --android-send-records <android_send_records.json> --output <bundle.json>
```

如果需要把 bundle 固定回 run artifact，也可以改用：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_taskmail_closeout_bundle.py <thread_id> --task-root .\tasks --android-send-records <android_send_records.json> --write-run-artifact
```

## Closeout Review Questions

第一轮 live 样本至少要回答这些问题：

1. accepted direct `/status` 是否稳定产出 canonical `[STATUS]` mail
2. accepted direct `reply` 是否稳定回到当前 canonical mail outcome，而不是停在 ack-only
3. `action_type`、`target_session_identity`、`request_id` 是否都能在 closeout 工件里机器可读地保留
4. `same_run_bind` 是否能在不依赖人工翻日志的前提下成立
5. subject、state capsule、terminal semantics 是否与 mail path 保持一致

## Switch-Blocker Reading

在 post-creation 这条线里，以下情况应继续读作 `switch_blocker`，而不是普通 evidence gap：

- accepted direct `/status` 没有落出 canonical `[STATUS]` mail
- accepted direct `reply` 无法稳定绑定到当前 run outcome
- `terminal_mail_subject`、state capsule 或 terminal semantics 与 mail path 漂移
- closeout 工件缺失关键锚点，导致 `request_id` / `ingress_message_id` / `last_summary` 无法稳定解释
- 需要靠人工翻 raw logs 才能判断 accepted action 到底是否成功闭环

若出现上述任一情况：

- 先不要改写 `docs/current/*`
- 先不要把 post-creation action 并入当前 Phase 4 `new_task` covered flow
- 先在 post-creation 这条线内部收口，再决定是否需要另起 mismatch note

## Layer 1 Upgrade Gate

只有在以下条件同时满足后，才建议考虑回写 `docs/current/*`：

- direct `/status` 已有 live closeout 样本，且 bundle 解释稳定
- direct `reply` 已有 live closeout 样本，且 bundle 解释稳定
- `same_run_bind` 已至少在一条 accepted `reply` 样本上稳定成立
- 没有发现 subject / state capsule / terminal semantics drift
- 没有发现新的 `fallback_required` 或 `switch_blocker`

满足这些条件后，建议按下面顺序推进：

1. 回写 `docs/current/mail_protocol.md`
2. 回写 `docs/current/android_runner_communication_contract.md`
3. 回写 `docs/current/README.md`
4. 再决定是否需要把 post-creation line 的 evidence 提升为 shared freeze / closeout authority

## Boundaries That Remain Unchanged

即使第一轮 live closeout 样本收齐，这些边界在本 handoff 下也仍然不变：

- current Phase 4 covered flow 仍先按 `new_task` 读取
- current Phase 5 open line 仍包括 fresh VPS acceptance
- quick answer / `Answers:` / paused `/resume` / attachment continuation 仍不属于本合同 v1
- targeted-session variant 与 cross-workspace switching 仍不属于本合同 v1
- `direct-default` 仍不能因为这条实现线闭环就自动宣告

## Current Conclusion

仓库侧 post-creation session-action v1 现在已经不再缺“下一步先写什么代码”的答案。

下一次会话默认起点应改为：

1. 先收两条 live closeout evidence
2. 再做 bind / blocker 判读
3. 最后才决定是否升级 `docs/current/*`

如果后续需要继续扩 post-creation scope，应另写新的 contract / scope note，而不是把 quick answer、`Answers:`、`/resume`、attachment continuation 或 targeted-session variant 继续隐式塞进当前 v1。

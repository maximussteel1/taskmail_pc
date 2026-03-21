# Phase 3 Direct Inbound Fixture Package (v1)

## Status

- Date: 2026-03-21
- Scope: Phase 3 第一份 active-session detail direct inbound 共享 fixture package 冻结
- Layer: Layer 2 cross-repo fixture note
- Related docs:
  - `docs/plans/phase3_direct_inbound_wire_v1.md`
  - `docs/plans/phase3_direct_inbound_mapping_v1.md`
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/current/task_view_mail_parsing_rules.md`
  - `docs/current/pc_mail_output_protocol.md`

## Purpose

`phase3_direct_inbound_wire_v1.md` 和 `phase3_direct_inbound_mapping_v1.md` 已经冻结了：

- Android 如何订阅 active session detail
- server 如何推送 `session_update`
- direct 与 mail 如何共存

这份文档继续把“共享 fixture package”写死，目标只有两个：

1. 让 Android 与仓库侧都能基于同一批 deterministic fixtures 接线
2. 让 identity fallback、question 语义、timeline suppress / replace 不再靠 prose 理解

这不是 replay API，也不是 relay 内部测试专用格式规范。

## Package Boundary

v1 fixture package 只覆盖以下范围：

- `subscribe_session_detail` request
- `packet_ack`
- ordered `session_update`
- 必要时的 mail-side companion item
- expected Android detail projection

v1 fixture package 不覆盖：

- direct `reply`
- workspace summary direct contract
- attachment binary sync
- history replay API
- Android UI screenshot baseline

## Fixture Unit Contract

每个 fixture unit 应至少包含以下五块：

1. `fixture_meta`
2. `subscribe_exchange`
3. `session_updates`
4. `mail_companion`
5. `expected_projection`

对于需要表达 resubscribe / refresh recovery 的 fixture，允许再额外包含：

6. `recovery_exchange`

建议最小 shape：

```json
{
  "fixture_meta": {
    "fixture_id": "sub_repo_workdir_session_running_snapshot",
    "schema_version": "phase3-direct-inbound-fixture-package-v1",
    "intent": "repo/workdir fallback resolves canonical workspace before first snapshot"
  },
  "subscribe_exchange": {
    "request": {},
    "ack": {}
  },
  "session_updates": [],
  "recovery_exchange": null,
  "mail_companion": {
    "items": []
  },
  "expected_projection": {
    "canonical_workspace_id": "workspace_a13f92d1c0ef",
    "canonical_session_id": "session_001",
    "canonical_thread_id": "thread_001",
    "header_status": "running",
    "question_set_id": null,
    "visible_business_event_keys": [],
    "suppressed_direct_business_event_keys": []
  }
}
```

字段约束：

- `fixture_meta.fixture_id`
  - required
  - 全 package 内唯一
- `fixture_meta.schema_version`
  - required
  - fixed to `phase3-direct-inbound-fixture-package-v1`
- `subscribe_exchange.request`
  - required
  - 必须符合 `phase3_direct_inbound_wire_v1.md` 的 subscribe request shape
- `subscribe_exchange.ack`
  - required
  - 必须是 `packet_ack` 或 reject ack
- `session_updates`
  - required array
  - 对 reject fixture 可为空
- `recovery_exchange`
  - optional
  - 仅用于 `gap_resubscribe_fresh_snapshot` 之类需要表达第二次 subscribe 的 fixture
  - when present, shape 与 `subscribe_exchange` 相同
- `mail_companion.items`
  - required array
  - 没有 mail companion 时也必须显式给空数组
- `expected_projection`
  - required
  - 描述 Android merge 层最终应得到的 detail 结果，而不是仅描述 direct 原始输入

## Determinism Rules

所有 fixture 都应遵守以下固定规则：

- 使用固定 `schema_version`
- 使用固定 ISO 时间戳，不依赖生成时当前时间
- `sequence` 在同一 fixture 内严格单调递增
- `update_id`、`subscription_id`、`packet_id`、`business_event_key` 必须固定，不允许按导出轮次漂移
- 若 request 使用 `repo_path` / `workdir` fallback，`session_update` 中仍只输出 canonical `workspace_id`
- 若 direct item 会被后续 mail durable item suppress，则 fixture 必须同时给出 direct item 与 mail companion item

## Identity Rules

本 package 统一把 identity 分成两层：

- subscribe input locator
- canonical output identity

其中：

- subscribe input locator 可以是 `workspace_id`，也可以是 `repo_path + workdir`
- session locator 可以是 `session_id`，也可以是 `thread_id`
- canonical output identity 永远回到 `workspace_id + session_id + thread_id`

fixture package 必须至少覆盖以下四种 locator 组合：

1. `workspace_id + session_id`
2. `workspace_id + thread_id`
3. `repo_path + workdir + session_id`
4. `repo_path + workdir + thread_id`

同时还必须覆盖以下两个 reject 组合：

1. 只有 `repo_path`，无法唯一解析 workspace
2. request 提供的 `workspace_id` 与 `repo_path/workdir` 解析结果冲突

## Mail Companion Rule

`mail_companion.items` 不是为了重放完整邮件，而是为了固定 cross-source reconciliation 结果。

每个 mail companion item 至少应给出：

- `source = mail`
- `business_event_key`
- `item_type`
- `status`
- `summary`
- `arrived_at`

只要这个 mail item 与 direct item 命中同一 `business_event_key`，`expected_projection.suppressed_direct_business_event_keys`
就必须把对应 direct key 列出来。

## Required Fixture Manifest

v1 共享 package 至少应包含以下 fixture units。

### A. Identity / Subscribe Cases

1. `sub_workspace_session_queued_snapshot`
   - locator: `workspace_id + session_id`
   - result: accepted
   - first snapshot status: `queued`
   - covers: current `accepted -> queued` wire normalization
2. `sub_workspace_thread_running_snapshot`
   - locator: `workspace_id + thread_id`
   - result: accepted
   - first snapshot status: `running`
   - covers: thread-based session resolution with canonical `session_id` returned in update
3. `sub_repo_workdir_session_running_snapshot`
   - locator: `repo_path + workdir + session_id`
   - result: accepted
   - first snapshot status: `running`
   - covers: workspace fallback resolution and canonical `workspace_id` echo in update
4. `sub_repo_workdir_thread_running_snapshot`
   - locator: `repo_path + workdir + thread_id`
   - result: accepted
   - first snapshot status: `running`
   - covers: workspace fallback + thread-based session resolution in one step
5. `sub_repo_only_reject_workspace_identity_unresolved`
   - locator: `repo_path` only
   - result: rejected
   - reject reason: `workspace_identity_unresolved`
6. `sub_workspace_locator_mismatch_reject`
   - locator: conflicting `workspace_id` and `repo_path/workdir`
   - result: rejected
   - reject reason: `workspace_identity_mismatch`

### B. Snapshot Status Cases

7. `snapshot_running_no_reply_preview`
   - status: `running`
   - timeline: empty or no assistant-visible output
8. `snapshot_running_with_reply_preview`
   - status: `running`
   - timeline: one `assistant_reply_preview`
9. `snapshot_waiting_single_question`
   - status: `awaiting_user_input`
   - question shape: single question with canonical `choices` and `choice_labels`
10. `snapshot_waiting_multi_question`
    - status: `awaiting_user_input`
    - question shape: multi-question set preserving canonical order
11. `snapshot_paused_from_question`
    - status: `paused`
    - `paused_from_status = awaiting_user_input`
12. `snapshot_done_terminal`
    - status: `done`
    - timeline: one direct `terminal_summary`
13. `snapshot_failed_terminal`
    - status: `failed`
    - timeline: one direct `terminal_summary`
14. `snapshot_killed_terminal`
    - status: `killed`
    - timeline: one direct `terminal_summary`

### C. Delta / Resync / Reconciliation Cases

15. `delta_state_transition_waiting`
    - start: `running`
    - delta: `state_transition` to `awaiting_user_input`
    - covers: `question_state` replacement and status flip
16. `delta_timeline_append_reply_preview`
    - start: `running`
    - delta: `timeline_append` with one `assistant_reply_preview`
    - covers: append-only preview flow
17. `mail_suppresses_direct_terminal_summary`
    - direct: one `terminal_summary`
    - mail companion: matching `[DONE]` / `[FAILED]` / `[KILLED]` durable receipt
    - covers: direct terminal item suppress / replace
18. `mail_suppresses_direct_question_prompt`
    - direct: one `question_prompt`
    - mail companion: matching `[QUESTION]` durable item
    - covers: question prompt suppress / replace
19. `mail_suppresses_direct_paused_hint`
    - direct: one `paused_hint`
    - mail companion: matching `[PAUSED]` durable item
    - covers: paused hint suppress / replace
20. `gap_resubscribe_fresh_snapshot`
    - start: accepted subscribe + first snapshot
    - then: one missing or non-applicable delta
    - recovery: Android resubscribe with `reason = detail_refresh`
    - result: fresh accepted subscribe + new snapshot

## Expected Projection Fields

为了让 Android 与仓库侧验证口径一致，`expected_projection` 至少应包含：

- `canonical_workspace_id`
- `canonical_session_id`
- `canonical_thread_id`
- `header_status`
- `header_lifecycle`
- `last_summary`
- `question_set_id`
- `pending_question_ids`
- `quick_answer_choices`
- `visible_business_event_keys`
- `suppressed_direct_business_event_keys`

说明：

- `quick_answer_choices` 必须是 `{ "value": "...", "label": "..." }` object array，而不是只给 canonical value string list
- `value` 必须等于 canonical choice value，`label` 必须等于 `choice_labels[value]`，若无 label override 则回退到 `value`
- `visible_business_event_keys` 表示 Android 最终 detail timeline 中应仍可见的 business events
- `suppressed_direct_business_event_keys` 表示 direct item 虽已收到，但最终不应显示
- terminal mail / question mail / paused mail 到达后的 suppress 结果必须落在这里，而不是只写在 prose 里

## Export Layout

当前仓库侧导出的实际 JSON fixture 目录形状如下：

```text
docs/plans/fixtures/phase3_direct_inbound_v1/
  manifest.json
  sub_workspace_session_queued_snapshot.json
  sub_workspace_thread_running_snapshot.json
  sub_repo_workdir_session_running_snapshot.json
  sub_repo_workdir_thread_running_snapshot.json
  sub_repo_only_reject_workspace_identity_unresolved.json
  sub_workspace_locator_mismatch_reject.json
  snapshot_running_no_reply_preview.json
  snapshot_running_with_reply_preview.json
  snapshot_waiting_single_question.json
  snapshot_waiting_multi_question.json
  snapshot_paused_from_question.json
  snapshot_done_terminal.json
  snapshot_failed_terminal.json
  snapshot_killed_terminal.json
  delta_state_transition_waiting.json
  delta_timeline_append_reply_preview.json
  mail_suppresses_direct_terminal_summary.json
  mail_suppresses_direct_question_prompt.json
  mail_suppresses_direct_paused_hint.json
  gap_resubscribe_fresh_snapshot.json
```

v1 这一步已经把第一批 representative JSON fixture 文件落到了这个目录下。

## Coordination Rule

Android 与仓库侧在开始实现前，应先对齐以下三件事：

1. fixture id 列表不再改名
2. `expected_projection` 字段集不再临时扩缩
3. identity fallback 的 reject reason 不再各自另起名字

如果后续发现某个 fixture 仍不足以表达当前语义，优先新增 fixture，不要静默改旧 fixture meaning。

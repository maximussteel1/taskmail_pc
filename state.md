# Project State

## Current Snapshot

- Updated At: 2026-03-24
- Current Runtime Stage: post-Phase-8 / TaskMail direct relay-control-file mainline in progress
- Current Planning Mainline: `VPS-first multi-PC control plane`
- Status: Active
- Current Truth Layer: `docs/current/*`
- Bootstrap Entry: `.\.venv\Scripts\python.exe -m mail_runner.app --once --config <mail_config.bot.local.yaml>`
- Hosted Loop Entry: `.\.venv\Scripts\python.exe -m mail_runner.host --config <mail_config.bot.local.yaml> --runtime-dir <_tmp_live_mail_runner>`
- Observability Entry: `.\.venv\Scripts\python.exe -m mail_runner.observe --config <_tmp_live_mail_runner\mail_config.loop_30s.yaml> status`
- Test Command: `.\.venv\Scripts\python.exe -m pytest`
- Latest Recorded Full-Suite Validation: `2026-03-24` -> `452 passed`
- Note: `2026-03-24` 已为 shared `/control` current-session session-action 扩展补跑 `.\.venv\Scripts\python.exe -m pytest`，结果 `452 passed`
- Note: `2026-03-24` 已在 `mail_config.bot.relay.local.yaml + _tmp_live_mail_runner` 这组 relay-enabled host 上，完成 Android 真机 `thread_023` fresh-session smoke；后续 current-session plain direct `reply` 与 current-session direct `/status` 两条样本都已收齐 Android retained send record、`session_action_closeout.json`、canonical mail 与 closeout bundle，且两条 bundle 的 `same_run_bind.effective_bind_level` 都稳定为 `request_id`

## Current Runtime Facts

- 当前系统仍是 mail-first：mail 是默认控制面、默认 receipt truth、默认 artifact/history truth
- PC 仍是 task execution truth；relay 不接管 task execution
- 当前正式 direct surface 仅限：
  - direct `new_task`
  - bootstrap `[SYNC]` `v1` / `v2`
  - shared `/control` current-session direct `/status`
  - shared `/control` current-session plain direct `reply`
  - shared `/control` relay-side `transport_probe`
  - current-session direct `/status`
  - current-session plain direct `reply`
  - active-session detail read sidecar
  - relay `/v1/files` oversized-artifact file surface
- current-session direct `/status` 与 plain `reply` 当前仍是 bridge-to-mail，不是新的 direct terminal-result API
- `/control` 当前会返回三类 direct result frame：
  - bootstrap `[SYNC]` `v2` 的 `bootstrap_result`
  - current-session direct `/status` / plain `reply` 的 `session_action_result`
  - relay-side `transport_probe` 的 `transport_probe_result`
- 其中 `session_action_result` 当前只表示 `mail_ingress_submission` 与 `session_action_closeout` 锚点快照，不替代最终 canonical mail outcome
- relay `/v1/files` 当前用于超阈值 artifact 的 relay-hosted external delivery；本地 artifact truth 仍保持在 `RunArtifact` + `artifact_index.json`
- 每轮 run 当前会落 `runs/<task_id>/canonical_summary.json`
- current-session direct `/status` 与 plain `reply` 当前会落 `session_actions/<request_id>/session_action_closeout.json`
- `scripts/build_taskmail_closeout_bundle.py` 当前可组装 `taskmail_daily_closeout_bundle.json`，用于 closeout / parity / bind 证据汇总
- direct post-creation resolver 当前优先读 `session_state`，缺失时允许回退 `thread_state`，但 identity 冲突会明确 reject

## Current Documentation Reading Order

1. 当前行为与协议：`docs/current/*`
2. TaskMail direct relay/control/file 当前事实：
   - `docs/current/taskmail_direct_control_file_contract.md`
   - `docs/current/android_runner_communication_contract.md`
   - `docs/current/mail_protocol.md`
3. 当前发出内容与附件/外链显示：
   - `docs/current/pc_mail_output_protocol.md`
   - `docs/current/multimedia_mail_protocol.md`
4. 当前主线、后继候选线、冻结线、closeout/handoff 导航：
   - `docs/plans/README.md`
   - `docs/plans/coding_backlog.md`

## Planning Status

- 当前代码行为仍是 mail-first，但 future-direction mainline 已切到 `VPS-first 多 PC 控制面`
- 旧的 `TaskMail direct relay/control/file` 相关文档现在应按 compatibility / closeout / migration reference 读法维护
- `2026-03-24` 的 `thread_023` 真机 smoke 与其他 direct closeout 证据仍然有效，但它们不再定义未来主线，只定义当前兼容行为与历史迁移材料
- `VPS ingress truth v1` 当前不再单独读成“主线之后的候选线”，而应作为新主线的前置参考
- `P9 HTML` 仍是冻结线，不会因为这次 authority reset 自动重新变成当前主线

## Next Candidate Lines

- 冻结 `PC <-> VPS` 字段级 schema
- 落地 `PC` 节点注册、心跳与 `workspace_snapshot`
- 落地 `command / event / output_chunk / result / artifact_manifest` 骨架
- 如需重启 HTML / P9，只能在新主线明确排期并显式 reopen 后进行，不能隐式借用旧 backlog 口径

## Historical Note

- 更早的 Phase 0-8 里程碑、relay bootstrap 演进、以及已明确结束的 closeout 记录，当前主要保留在 `git log` 与 `docs/plans/*` 对应 handoff/evidence 文档中

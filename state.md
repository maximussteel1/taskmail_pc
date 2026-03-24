# Project State

## Current Snapshot

- Updated At: 2026-03-24
- Current Runtime Stage: post-Phase-8 / TaskMail direct relay-control-file mainline in progress
- Status: Active
- Current Truth Layer: `docs/current/*`
- Bootstrap Entry: `.\.venv\Scripts\python.exe -m mail_runner.app --once --config <mail_config.bot.local.yaml>`
- Hosted Loop Entry: `.\.venv\Scripts\python.exe -m mail_runner.host --config <mail_config.bot.local.yaml> --runtime-dir <_tmp_live_mail_runner>`
- Observability Entry: `.\.venv\Scripts\python.exe -m mail_runner.observe --config <_tmp_live_mail_runner\mail_config.loop_30s.yaml> status`
- Test Command: `.\.venv\Scripts\python.exe -m pytest`
- Latest Recorded Full-Suite Validation: `2026-03-24` -> `452 passed`
- Note: `2026-03-24` 已为 shared `/control` current-session session-action 扩展补跑 `.\.venv\Scripts\python.exe -m pytest`，结果 `452 passed`

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

- repository-side `TaskMail direct relay/control/file` 这条线仍是当前主线；current behavior 已大面积落地到代码和 `docs/current/*`，但整条线尚未闭环
- `phase2/phase3/phase4`、post-creation、taskmail control/file 相关文档不应被误读成“纯历史”；它们当前仍是这条主线的 closeout / evidence / handoff 支撑资料
- `P9 HTML` 仍是冻结线，不会因为这次 closeout 自动重新变成当前主线
- `VPS ingress truth v1` 当前仍是后继候选线，不是当前主线

## Next Candidate Lines

- `VPS ingress truth v1`
- 基于 current closeout evidence，决定是否升级 current-session direct `/status` / plain `reply` 的 Layer 1 读法
- 如需重启 HTML / P9，只能在当前主线明确收口并显式 reopen 后进行，不能隐式借用旧 backlog 口径

## Historical Note

- 更早的 Phase 0-8 里程碑、relay bootstrap 演进、以及已明确结束的 closeout 记录，当前主要保留在 `git log` 与 `docs/plans/*` 对应 handoff/evidence 文档中

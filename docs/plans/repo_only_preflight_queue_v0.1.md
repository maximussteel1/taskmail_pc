# 联调前 Repo-Only 收口队列（v0.1）

## Status

- Date: 2026-03-27
- Scope: Android / operator 联调前，本仓可单独推进的 repo-side 收口项
- Layer: planning / execution note
- Source of truth:
  - `docs/current/*`
  - `docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`
  - `docs/plans/vps_only_checkpoint_reenable_validation_runbook_v0.1.md`
  - `docs/plans/vps_file_surface_cutover_and_cos_decommission_checklist_v0.1.md`
  - `state.md`

## 目的

这份小计划只回答一件事：

- 在不等待 Android 新一轮联调的前提下，本仓接下来先把哪些 repo-only 项收硬

它不是新的主线 authority，也不重写当前协议边界。

## 当前判断

截至 `2026-03-27`，当前 repo-side 不应再把“继续补单条 Android 样本”当成唯一前置条件。

更合理的读法是：

- `VPS-first` Phase 1 A-H first-pass 骨架已经立住
- 当前更值得优先收硬的是：
  - `vps_only` checkpoint 的恢复验证入口
  - `/v1/files` owner lane 的观察窗口与 cutover 口径
- Android-facing `POST /v1/android/create-session` 不是“还没落地”的空白 seam；repo-side 已有 contract 与 roundtrip test

## 本轮范围

本轮先执行两项：

1. `vps_only` checkpoint 单入口验证
2. `/v1/files` owner lane 观察窗口验证收硬

暂不在本轮展开：

- 更高层多 `PC` observer / subscription
- `OpenCode` true incremental streaming
- `COS` 真正删除

## 补充执行（2026-03-27）

在 `1 + 2` 完成后，下一条 repo-only 收口项改为：

3. `hybrid / vps_only` active-mode boundary 回归

本轮补充交付口径：

- `vps_only` 下 shared `/control` 的 `hello_ack.accepted_payload_schemas` 只保留 bootstrap `v2`
- bootstrap `v2` 仍保持可用
- current-session `status|reply` 与 relay-side `transport_probe` 在该模式下返回 `unsupported_action`
- Android-facing `POST /v1/android/create-session` 仍通过 `pc-control` lane 保持可用

## 继续执行（2026-03-27）

在 `3` 之后，继续推进：

4. `pc-control` operator/read-side 与多 `PC` observer first pass

本轮补充交付口径：

- repo-side 补一个配套的 operator read-side CLI，而不是只保留裸 HTTP endpoint
- read-side 覆盖 `nodes / workspaces / commands / lease / ingress / terminal-outcome`
- 把 `lease / ingress / terminal-outcome` 三条 operator 观察面补成稳定 HTTP 回归

## 继续执行（2026-03-27，第二阶段）

在 `4` 之后，继续推进：

5. `OpenCode` true incremental streaming
6. `COS` 退场准备 gate 收硬

本轮补充交付口径：

- `OpenCode SDK` adapter 现在会在 turn 期间直接消费 OpenCode `event` SSE，把 `message.part.updated / session.idle` 收成 same-layer `turn.started -> assistant.delta* -> assistant.completed -> turn.completed`
- `sdk_stream_smoke` 现在会以 `sdk_turn.json.stream_mode=event_stream_message_parts_incremental` 作为 `supports_incremental_stream=true` 的判定；只有 future run 回退到非 incremental `stream_mode` 时，才继续写 `incremental_stream_not_proven`
- `/v1/files` 观察窗口报告现在除 `window_ready` 外，还会输出 `cos_decommission_checks` / `cos_decommission_candidate`
- CLI 现在支持 `--require-cos-decommission-candidate`，可把“观察窗口是否已达到 `COS` 删除准备”直接当成 gate

## 1. `vps_only` checkpoint 单入口验证

目标：

- 把“恢复后最小 owner-seam 是否成立”固定成单一 repo-side 入口

本轮交付口径：

- 一个可重复执行的本地脚本入口
- 输出统一 JSON 结果，而不是靠人工拼多条命令
- 最少覆盖：
  - `GET /healthz`
  - `GET /debug/pc-control/nodes`
  - `GET /debug/pc-control/workspaces`
  - relay `/v1/files` upload + metadata/content roundtrip
  - 旧 direct `new_task` 在 `vps_only` 下返回 `unsupported_action`

本轮不要求：

- 直接把 operator 观察窗口也并入同一条脚本
- 删除旧兼容实现

## 2. `/v1/files` owner lane 观察窗口验证收硬

目标：

- 把“观察窗口是否干净”从人工读 JSON，提升到 repo-side 可计算结论

本轮交付口径：

- 现有 `external_delivery_window_report` 补足 cutover 关心字段
- 报表至少能直接回答：
  - provider 是否符合 `file_surface / oversize->cos`
  - 当前 artifact kind 是否仍落在 `image | file`
  - 候选 `artifact_manifest.download_ref_source` 是否与 `external_delivery_index.<provider>` 一致
  - 当前窗口能否判定为 `window_ready`
- CLI 允许在需要时对“不干净窗口”返回非零退出码

本轮不要求：

- 直接证明所有 target deployment 都已不需要 `COS`
- 从真实业务邮件正文反推 `delivery_notices`

## 建议执行顺序

1. 先落文档与脚本入口，固定单一读法。
2. 再补单测，把 `vps_only` / owner-lane 判定写成稳定回归。
3. 最后跑 repo-side 定向测试，确认脚本与报告在本地 fixture 上成立。

## 完成定义

当以下事项都成立时，可认为本轮完成：

- 已有短计划文档可供 handoff
- `vps_only` 单入口验证脚本已落地
- `external_delivery_window_report` 已能直接给出 `window_ready`
- 对应测试已补齐并通过

截至 `2026-03-27`，`1 -> 6` 的 repo-only first pass 已全部落地。当前剩余项不再是继续补 repo-side 骨架，而是：

- 已对真实 deployment 跑出 `window_ready / cos_decommission_candidate` 观察窗口：
  - `window_ready=true` 已通过
  - `cos_decommission_candidate=false`，当前唯一阻塞是两条历史 oversize `COS` delivery 仍在观察窗口内
- [x] 已验证 `/v1/files` transport-token consumer/cutover 读法，并把近期联调目标明确收敛到“要求 `window_ready`，不要求当前 deployment 立即达到 `cos_decommission_candidate`”
- 继续补更高层多 `PC` observer / subscription 证据

# Next Development Plan

## Status

- Date: 2026-03-24
- Scope: repository-side current-mainline note after the latest TaskMail direct relay/control/file landing
- Source of truth: `docs/current/*`, `docs/plans/README.md`, `state.md`

## Current Reading

- repository-side `TaskMail direct relay/control/file` 这条线仍是当前主线
- 这条线已经有较大一段 current behavior 落到代码和 `docs/current/*`
- 但这条线尚未闭环，不应被误读成“已完成后退场”
- `phase2/phase3/phase4`、post-creation、taskmail control/file 文档当前仍是这条主线的 closeout / evidence / handoff 资料
- `P9 HTML` 与 broader outbound convergence 不是当前默认编码队列

## Current Mainline

当前主线已落地的 repository-side 直连切片包括：

- direct `new_task`
- bootstrap `[SYNC]` `v1` / `v2`
- shared `/control` bootstrap `v2`
- shared `/control` relay-side `transport_probe`
- current-session direct `/status`
- current-session plain `reply`
- active-session detail read sidecar
- relay `/v1/files` oversized-artifact delivery
- `canonical_summary.json`
- `session_action_closeout.json`
- `taskmail_daily_closeout_bundle.json`

这些内容现在应同时按两层来阅读：

- 作为当前行为：看 `docs/current/*`
- 作为尚未闭环主线的证据与 closeout 支撑：看 `docs/plans/*` 对应文档

当前补充读法：

- Android 仓当前已经把 transport readiness / observability harness 的首轮真机闭环补齐
- 这轮正向证据覆盖：
  - `/control transport_probe` 的 `command_ack -> event* -> result` 与 same-id replay continuity
  - `/v1/files` debug-only 单样本的 `POST -> GET metadata -> GET content -> sha256`
- 这组证据当前只说明 transport/debug harness 已闭到真机，不等于 `new_task` / `reply` / `[SYNC]` 已完成 `/control` 业务 cutover

## Still Open On This Mainline

当前至少仍保留这些未闭环项：

1. closeout / live acceptance 仍未被 owner 判定为闭环完成
2. current-session direct `/status` / plain `reply` 的 Layer 1 读法是否进一步升级，仍是 open decision
3. 当前主线相关 evidence / handoff 文档仍要继续作为活动资料使用，而不是直接降级为纯历史
4. `/v1/files` 当前虽然已有 Android 真机单样本文本文件 smoke，但更宽文件类型、业务链路接入与批量/异常矩阵仍未闭环
5. `transport_probe` 当前虽然已有 Android 真机 same-id replay 正向样本，但它仍只应按 operator/debug harness 读取

## Candidate Next Line

在当前主线闭环之后，最明确的后继候选线是：

1. `VPS ingress truth v1`
   - 参考：
     - `docs/plans/vps_ingress_truth_v1_checklist.md`
     - `docs/plans/vps_ingress_truth_v1_execution_order.md`

补充：

- 如需重启 HTML / `P9`，只能在 owner 明确 reopen 后启动
- 不能因为旧文档仍在仓库里，就默认把它读回 active queue

## Not Current Queue

以下内容当前明确不应读作 active implementation queue：

- `p9_html_mail_projection_plan.md`
- `android_consumer_contract_alignment_plan.md`
- `android_consumer_protocol_freeze_note.md`
- `outbound_mail_contract_convergence_plan.md`
- `phase5_*` 文档集

## Guardrails

后续开发继续遵守以下边界：

1. 当前行为 truth 永远以 `docs/current/*` 为准
2. 不要把当前主线误降级成“已完成旧线”，也不要隐式重开真正已完成的旧切片
3. mail 仍是默认控制面、receipt truth、artifact/history truth
4. PC 仍是 task execution truth
5. 不把 `artifact_index.json` 污染成 transport/file-plane 主库
6. 如果要开始一条新的主线，应先明确当前主线是否已经闭环，再决定是新写 plan / handoff，还是继续沿当前主线文档推进

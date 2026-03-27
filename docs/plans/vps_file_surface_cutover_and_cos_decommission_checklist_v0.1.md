# `/v1/files` Cutover / `COS` 退场清单（v0.1）

## Status

- Date: 2026-03-27
- Scope: repository-side artifact external-delivery cutover / decommission checklist
- Layer: planning checklist
- Source of truth:
  - `docs/current/taskmail_direct_control_file_contract.md`
  - `docs/current/multimedia_mail_protocol.md`
  - `docs/reference/artifact_contract_smoke_validation.md`
  - `docs/plans/android_pc_vps_evolution_authority.md`
  - `docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`

## 目的

这份清单只回答一件事：

- 什么时候可以把 artifact external-delivery 的 owner lane 切到 `VPS /v1/files`
- 什么时候可以把 `COS` 从“临时兼容线”推进到“删除准备”与“真正退场”

它不负责重新定义当前 truth layer，也不负责重写 mail-first 当前行为。

## 当前已知事实

截至 `2026-03-27`，仓库里已经成立的前提有：

- [x] repo-side 已支持 `external_delivery_backend_preference=auto|cos|file_surface`
- [x] repo-side 默认 owner preference 已切到 `file_surface`；当 relay `/v1/files` lane 可用时，runtime 默认优先走该 lane
- [x] `external_delivery_backend_preference=auto` 现在只保留为显式 legacy 兼容语义；当 `COS` 仍保留配置时，它会继续维持旧的 `COS`-first 选择
- [x] 当 artifact 超过当前 live `/v1/files` 单文件上限且 `COS` 仍可用时，cutover 期间 runtime 现在会只对这类 oversize artifact 保留 `COS` 兼容交付
- [x] 本地 truth layer 仍保持为 `RunArtifact + artifact_index.json`
- [x] transport-facing `artifact_id -> file_id` 绑定仍单独放在 `artifact_file_binding_index.json`
- [x] successful external delivery 的 provider/url 证据已单独放在 `external_delivery_index.json`
- [x] `scripts/external_delivery_window_report.py` 现在已能直接给出 `window_ready`、`cos_decommission_checks` 与 `cos_decommission_candidate`
- [x] `scripts/external_delivery_window_report.py` 现在支持 `--require-clean-window` 与 `--require-cos-decommission-candidate` 两种 gate 读法
- [x] local fixture 已验证 live local relay `/v1/files` roundtrip
- [x] live VPS relay host 已验证 `/v1/files` upload + metadata GET + content GET roundtrip
- [x] 当前 live evidence 已证明 `/v1/files` owner lane 不要求先删除 `COS` 配置

当前仍要记住的现实约束有：

- [x] 当前 `/v1/files` 单文件上传上限仍是 `32 MiB`
- [ ] 当前 live runtime 对 upload metadata 的 `kind` 只接受 `image | file`
- [x] 如果目标 deployment 仍有超 `32 MiB` 文件，当前 cutover 应读成“`/v1/files` owner lane + oversize 仍暂走 `COS` 兼容线”，而不是“所有 artifact 都已可立即脱离 `COS`”

## 现有证据

- repo 内 local fixture 证据：
  - `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_file_surface_preferred/smoke_result.json`
- 真实 VPS relay host 证据：
  - `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_live_vps_file_surface/smoke_result.json`
- 真实 live business-run owner-lane 证据：
  - `_tmp_live_mail_artifact_probe/artifact-probe-v2-20260325_210034-f82f17/summary.json`
  - `22 MiB` 样本在 `external_delivery_backend_preference=file_surface` 下命中 `provider=file_surface`
- 真实 live business-run oversize 兼容证据：
  - `_tmp_live_mail_artifact_probe/artifact-probe-codex-oversize-20260325_210950-b9f355/summary.json`
  - `34 MiB` 样本在同一套 live 配置下命中 `provider=cos`
- 首份 live 观察窗口报告：
  - `_tmp_live_mail_runner/external_delivery_window_report_20260325.json`
  - 当前已记录 `2` 条真实 external-delivery run，`expectation_mismatch_count=0`
- 最新 live gate 结果（`2026-03-27`）：
  - `_tmp_vps_only_checkpoint_validation/vps-only-checkpoint-validation-20260327_live_rerun/validation_result.json`
  - `_tmp_live_mail_runner/external_delivery_window_report_20260327_clean_gate.json`
  - `_tmp_live_mail_runner/external_delivery_window_report_20260327_cos_gate.json`
  - 当前结论：真实 deployment 已达到 `window_ready=true`，但 `cos_decommission_candidate=false`；唯一阻塞是观察窗口里仍保留两条 oversize `COS` delivery
- 当前 transport-token consumer 证据（`2026-03-27`）：
  - `_tmp_file_surface_consumer_smoke/file-surface-consumer-smoke-20260327_live_vps_authenticated_consumer/smoke_result.json`
  - 当前结论：`download_ref_source=external_delivery_index.file_surface` 的 `GET download_ref` 在携带 transport token 时返回 `200`，缺 token / 错 token 返回 `401 unauthorized`

## A. `/v1/files` Cutover 前置门槛

以下门槛都满足后，才应把某个 target deployment 读成“可切换到 `/v1/files` owner lane”：

- [x] runtime 已支持 `external_delivery_backend_preference=file_surface`
- [x] 真实 relay host `/v1/files` 已完成至少一条 upload + metadata/content roundtrip 留证
- [x] `artifact_manifest` 已可从 `external_delivery_index.file_surface` 投影 `download_ref`
- [ ] target deployment 的真实 artifact 尺寸分布已确认不再需要 `COS` 承接超过 `32 MiB` 的单文件 external-delivery
- [ ] target deployment 的真实 artifact 类型分布已确认可映射到当前 `image | file` 约束
- [x] 当前 repo-side transport-token consumer 路径已确认能消费 `/v1/files` download URL，而不是仍隐含依赖 `COS`-specific contract
- [ ] Android end-user current contract 是否要把这条 auth-protected `/v1/files` 读法升级成更宽 app-facing seam，仍不在本次 cutover 结论内
- [ ] operator 已接受 cutover 期间先“保留 `COS` 配置但不默认使用”，而不是要求同一批次立即删干净

## B. `/v1/files` Cutover 执行清单

建议按以下顺序执行 cutover：

1. 先保留 `COS` 配置，不做同批次删除。
2. 让 target deployment 使用 repo-side 默认 `external_delivery_backend_preference=file_surface`；
   只有当前 deployment 仍要显式保留 legacy `COS`-first 读法时，才继续写 `external_delivery_backend_preference=auto` 或 `cos`。
   如果当前 deployment 仍存在超过 `32 MiB` 的 artifact，则把这一步读成“切 owner preference”，不是“同批次删除 `COS`”。
3. 使用现有 host 维护路径重启 runner，不自行拼临时命令。
4. 重启后先确认 host state 与 relay task-root sync 都健康。
5. 跑一条 live `/v1/files` smoke，确认 owner lane 真正命中 relay host。
6. 再观察真实 artifact 流量，确认成功 external delivery 持续来自 `file_surface`。

建议使用的现有命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 status -ConfigPath .\mail_config.bot.relay.local.yaml -RuntimeDir .\_tmp_live_mail_runner -NoPopup
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_mail_runner.ps1 restart -ConfigPath .\mail_config.bot.relay.local.yaml -RuntimeDir .\_tmp_live_mail_runner -NoPopup
```

```powershell
.\.venv\Scripts\python.exe .\scripts\artifact_contract_smoke.py --config .\mail_config.bot.relay.local.yaml --run-name artifact-contract-smoke-<date>_live_vps_file_surface
```

```powershell
.\.venv\Scripts\python.exe .\scripts\file_surface_consumer_smoke.py --config .\mail_config.bot.relay.local.yaml --run-name file-surface-consumer-smoke-<date>_live_vps_authenticated_consumer
```

```powershell
.\.venv\Scripts\python.exe .\scripts\external_delivery_window_report.py --config .\mail_config.bot.relay.local.yaml --limit-runs 20 --output .\_tmp_live_mail_runner\external_delivery_window_report_<date>.json
```

如果当前要把观察窗口直接当成 cutover gate，可改用：

```powershell
.\.venv\Scripts\python.exe .\scripts\external_delivery_window_report.py --config .\mail_config.bot.relay.local.yaml --limit-runs 20 --require-clean-window --output .\_tmp_live_mail_runner\external_delivery_window_report_<date>.json
```

如果当前要把“是否已达到 `COS` 删除准备”直接当成 gate，可改用：

```powershell
.\.venv\Scripts\python.exe .\scripts\external_delivery_window_report.py --config .\mail_config.bot.relay.local.yaml --limit-runs 20 --require-cos-decommission-candidate --output .\_tmp_live_mail_runner\external_delivery_window_report_<date>.json
```

如果当前阶段故意不保持服务持续在线，而是先冻结 `vps_only` checkpoint，再等待下一次集中 bring-up，则恢复服务和最小 smoke 应统一走：

- `docs/plans/vps_only_checkpoint_reenable_validation_runbook_v0.1.md`

cutover 后应至少确认这些结果：

- [x] `manage_mail_runner.ps1 status` 显示 runner 与 relay task-root sync 都健康
- [ ] live smoke 成功，且 `delivery_count=1`
- [ ] live smoke 的 `metadata_status=200`
- [ ] live smoke 的 `download_status=200`
- [ ] live smoke 的 `download_verified=true`
- [x] `download_ref_source=external_delivery_index.file_surface` 的 current consumer smoke 已确认携带 transport token 的 `GET download_ref` 返回 `200`
- [x] current consumer smoke 已确认缺 token / 错 token 的 `GET download_ref` 返回 `401 unauthorized`
- [x] 已至少有一条真实 run 产生 `external_delivery_index.json`，且 `22 MiB` 样本命中 `provider=file_surface`
- [x] 已至少有一条真实 oversize run 产生 `external_delivery_index.json`，且 `34 MiB` 样本命中 `provider=cos`
- [ ] 真实 run 产生的 `external_delivery_index.json` 里，provider 已在观察窗口内稳定为 `file_surface`
- [ ] 对于仍超过 `32 MiB` 的 artifact，真实 run 的 `external_delivery_index.json` 已在观察窗口内只在这些 oversize 样本上继续出现 `provider=cos`
- [ ] 真实 run 产生的候选 `artifact_manifest.download_ref_source` 稳定来自 `external_delivery_index.file_surface`
- [ ] 真实 run 没有出现意外 `delivery_notices`

## C. `COS` 进入删除准备的门槛

repo-side 现在对这部分的统一读法是：

- `window_ready=true` 只代表 owner-lane 观察窗口干净，不代表 `COS` 已可删除
- `cos_decommission_candidate=true` 才代表“当前请求窗口内已经没有 `COS` delivery，且 repo-side 所需门槛都满足”
- 即使 `cos_decommission_candidate=true`，这也只是“进入删除准备”的 gate，不等于应该立刻删除实现

只有在下面这些条件满足后，才应把 `COS` 从“兼容保留”推进到“删除准备”：

- [ ] 至少一个 target deployment 已把 `external_delivery_backend_preference=file_surface` 跑稳
- [ ] 观察窗口内没有因为 `/v1/files` owner lane 而被迫回切到 `COS`
- [ ] 当前目标 artifact 尺寸/类型矩阵不再要求 `COS` 为 oversize 或额外类型样本提供兼容能力
- [x] planning / config 口径已改成不再把 `COS` 写成默认 external-delivery lane；`auto` 只保留为显式 legacy 兼容值
- [ ] live `COS` evidence 不再是 merge gate / cutover gate
- [ ] operator 能接受“`COS` 只在明确例外部署中暂留，而不是默认全局保留”

## D. `COS` 真正退场时要做的事

当 `COS` 已进入真正退场阶段，建议按这个顺序收：

1. 先删“默认 owner lane”语义。
2. 再删“默认配置示例”与“默认验证入口”。
3. 最后删 routing / config / smoke / docs 中仅为兼容保留的实现。

更具体地说，退场动作至少包括：

- [ ] 停止在 planning 文档里把 `COS` 写成默认 external-delivery lane
- [ ] 停止在示例配置/部署说明里要求准备 `COS` 凭据
- [ ] 停止把 live `COS` evidence 当成常规验证项
- [ ] 删除 `external_delivery.py` 中仅为 `COS` 保留的 owner-lane 路由分支
- [ ] 删除 `AppConfig` 中仅为 `COS` 保留的配置字段与校验
- [ ] 删除仅为 `COS` 保留的 smoke / tests / docs
- [ ] 保证删除后仍不改变 `RunArtifact + artifact_index.json` 的 truth-layer 角色

## E. 当前不应混入这份清单的事

以下事项不属于这份 cutover / decommission checklist：

- mail control-plane 的退场清单
- `artifact_manifest` canonical 字段冻结
- 多 `PC` 路由 / 订阅层的高层证据
- `OpenCode` true incremental streaming 增强验证
- 反向把 control-plane packet 当成 artifact truth layer

## 当前建议

按今天仓库现实，下一步应这样读：

- `/v1/files` owner lane 的 live artifact evidence 已经不再只停在 smoke；当前 live deployment 已经补齐 `22 MiB -> file_surface` 与 `34 MiB -> cos` 两类真实业务样本
- repo-side 默认 owner preference 现在已经切到 `file_surface`；继续显式写 `auto` 应读成“保留 legacy COS-first”，而不是“当前默认行为”
- 现在更应该做的是把 target deployment 的观察窗口收硬，确认这不是一次性样本
- 现在更准确的做法是：先把真实 deployment 跑成稳定 `window_ready`，再看能否进一步跑成 `cos_decommission_candidate=true`
- 截至 `2026-03-27`，当前 deployment 已经满足前半句：`window_ready=true` 已在 live gate 上通过；后半句仍卡在两条历史 oversize `COS` 样本，因此当前更合理的读法是“允许继续 `/v1/files` owner-lane 联调，但不要提前删除 `COS`”
- 如果当前阶段故意让服务保持离线，不必为了“继续积累 live 样本”反复 bring-up；更合理的读法是先冻结 checkpoint、runbook 与验证口径，等下一次集中恢复服务时按单一入口完成最小 owner-seam 验证
- 如果当前 deployment 仍包含 `>32 MiB` 样本，可先接受“`/v1/files` owner lane + oversize 临时走 `COS`”这一级 cutover
- `COS` 暂时保留为兼容线，但默认不应继续投资成长期双通道
- 只有当前真实部署仍明确依赖 `COS` 且现有单条 live oversize 样本还不够时，才继续补更多 live `COS` evidence

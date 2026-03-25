# Artifact Contract Smoke 验证结果

## 目的

本文记录 artifact contract smoke 当前已经实际测到什么、证据在哪，以及当前剩余 gap 如何记录。

## 本次记录对应环境

- 日期：`2026-03-25`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- smoke 入口：`.\.venv\Scripts\python.exe .\scripts\artifact_contract_smoke.py`

## 已完成验证

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\artifact_contract_smoke.py --run-name artifact-contract-smoke-20260325_file_surface_preferred
```

结果：

- 状态：成功
- smoke 类型：local fixture + live relay file-surface roundtrip
- external-delivery route：显式 `external_delivery_backend_preference=file_surface`
  说明：这次 smoke 不再依赖“本机没有 COS 配置”这个前提；即使部署仍暂留 COS 配置，owner-lane cutover 也可以稳定优先走 `/v1/files`
- `artifact_index.json`：已生成
- `artifact_file_binding_index.json`：已生成
- `external_delivery_index.json`：已生成
- 解析出的 artifacts：2 个
  - `artifact-preview` / `image/png`
  - `artifact-report` / `text/markdown`
- skipped：1 条
  说明：manifest 中缺失文件被保留为 machine-readable skipped message
- 候选 `artifact_manifest`：已投影
  说明：`size` 可直接由本地文件字节数推导；`artifact-preview` 的 `download_ref` 当前来自 live `external_delivery_index.file_surface`，不是只来自手写 binding sidecar
- live relay `/v1/files`：已跑通
  说明：本次本地 server 实际上传成功 1 个 artifact，投影出的最终 URL 形如 `http://127.0.0.1:<port>/v1/files/<file_id>/content`
- 清理检查：通过
  说明：本次本地 relay file-surface server 已正常关闭，结果文件显式记录 `cleanup.required=true`

## 真实 VPS owner-lane cutover 证据

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\artifact_contract_smoke.py --config .\mail_config.bot.relay.local.yaml --run-name artifact-contract-smoke-20260325_live_vps_file_surface
```

结果：

- 状态：成功
- smoke 类型：live relay-host `/v1/files` owner-lane cutover evidence
- config：`mail_config.bot.relay.local.yaml`
- external-delivery route：强制 `external_delivery_backend_preference=file_surface`
  说明：这次 smoke 会在 live 模式下额外强制 `external_delivery_threshold_mb=0`，因此不依赖真实部署当前的阈值配置
- live relay host：`ws://124.223.41.153:8787/relay`
- live file-surface URL：`http://124.223.41.153:8787/v1/files`
- live upload：成功
  说明：本次成功 externalize 了 `artifact-preview`
- metadata GET：成功
  说明：`GET /v1/files/<file_id>` 返回 `200`，并且 `artifact.file_id` 与 binding / delivery sidecar 一致
- content GET：成功
  说明：`GET /v1/files/<file_id>/content` 返回 `200`，下载字节与本地 `preview.png` 一致
- `artifact_file_binding_index.json`：已生成
- `external_delivery_index.json`：已生成
- 候选 `artifact_manifest`：已投影
  说明：`artifact-preview.download_ref` 当前来自 live `external_delivery_index.file_surface`
- 清理检查：通过
  说明：live 模式没有启动本地 relay fixture，因此结果里记录为 `cleanup.required=false`

## 真实 live deployment 业务样本补充证据

这部分不是 `artifact_contract_smoke.py` 本身生成的 fixture，而是当前 live deployment 在同一套 cutover 配置下，额外补跑的真实业务样本。

样本 1：普通 owner-lane `/v1/files`

- 配置：`mail_config.bot.relay.local.yaml`
- live preference：`external_delivery_backend_preference=file_surface`
- 样本：`22 MiB` `owner_lane_probe.bin`
- backend：`opencode`
- 线程 / task：`thread_110` / `20260325_210103_9eae`
- 结果：成功
- external-delivery provider：`file_surface`
- 说明：terminal status mail、`artifact_file_binding_index.json`、`external_delivery_index.json` 三处都一致指向 relay `/v1/files`

样本 2：同配置下的 oversize `COS` 兼容交付

- 配置：`mail_config.bot.relay.local.yaml`
- live preference：`external_delivery_backend_preference=file_surface`
- 样本：`34 MiB` `owner_lane_probe_oversize.bin`
- backend：`codex`
- 线程 / task：`thread_112` / `20260325_211000_7c92`
- 结果：成功
- external-delivery provider：`cos`
- 说明：同一套 live cutover 配置下，`>32 MiB` 样本没有再命中 `/v1/files`，而是按兼容语义落到 `COS`

## 当前已确认结论

1. 当前仓库的本地 artifact truth 仍然是 `RunArtifact + artifact_index.json`。
2. transport-facing 绑定仍与 artifact truth 分层，继续单独写在 `artifact_file_binding_index.json`。
3. 成功 external delivery 的 provider/url 级 evidence 现在还会单独写在 `external_delivery_index.json`。
4. 当前最小可复用的候选 `artifact_manifest` 投影里，`artifact_id / kind / name / content_type / size` 都可以从现有 truth layer 稳定推出。
5. `download_ref` 不是当前本地 artifact truth 的内建字段；现在它优先来自 `external_delivery_index`，缺失时再回退到 `artifact_file_binding_index`。
6. relay `/v1/files` owner lane 现在不仅能在 repo 内本地 fixture 里成立，也已经在真实 VPS relay host 上完成 upload + metadata/content roundtrip 留证。
7. 在 live deployment 已切到 `external_delivery_backend_preference=file_surface` 后，普通 `22 MiB` 业务样本已证明 owner lane 可以真实稳定命中 relay `/v1/files`。
8. 同一套 live 配置下，`34 MiB` oversize 业务样本已证明兼容语义会把 `>32 MiB` artifact 留在 `COS`，而不是错误地继续走 `/v1/files`。
9. 因此，当前 target deployment 对 artifact lane 的正确读法应是：`/v1/files` 已是 owner preference，`COS` 只剩 oversize 兼容用途。
10. “未绑定 artifact 没有 download_ref” 目前应被视为已知 gap，而不是异常。

## 证据

- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_file_surface_preferred/smoke_result.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_file_surface_preferred/tasks/thread_001/runs/task_001/artifacts/artifact_index.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_file_surface_preferred/tasks/thread_001/runs/task_001/artifacts/artifact_file_binding_index.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_file_surface_preferred/tasks/thread_001/runs/task_001/artifacts/external_delivery_index.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_file_surface_preferred/tasks/thread_001/runs/task_001/artifacts/manifest.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_live_vps_file_surface/smoke_result.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_live_vps_file_surface/tasks/thread_001/runs/task_001/artifacts/artifact_index.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_live_vps_file_surface/tasks/thread_001/runs/task_001/artifacts/artifact_file_binding_index.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_live_vps_file_surface/tasks/thread_001/runs/task_001/artifacts/external_delivery_index.json`
- `_tmp_artifact_contract_smoke/artifact-contract-smoke-20260325_live_vps_file_surface/tasks/thread_001/runs/task_001/artifacts/manifest.json`
- `_tmp_live_mail_artifact_probe/artifact-probe-v2-20260325_210034-f82f17/summary.json`
- `_tmp_live_mail_runner/tasks/thread_110/runs/20260325_210103_9eae/artifacts/artifact_file_binding_index.json`
- `_tmp_live_mail_runner/tasks/thread_110/runs/20260325_210103_9eae/artifacts/external_delivery_index.json`
- `_tmp_live_mail_artifact_probe/artifact-probe-codex-oversize-20260325_210950-b9f355/summary.json`
- `_tmp_live_mail_runner/tasks/thread_112/runs/20260325_211000_7c92/artifacts/external_delivery_index.json`

## 当前未覆盖项

以下内容还没有纳入这条 artifact smoke：

- `vps-only` canonical `artifact_manifest` 正式字段冻结
- 多 artifact 多 binding / delivery 的更复杂 supersede 策略
- `artifact_contract_smoke.py` 本身仍没有直接生成 `>32 MiB` oversize 样本；当前这部分证据来自 live deployment 业务样本，而不是 fixture

因此，这轮结论目前只覆盖：

- 单机本地 Windows
- 当前本地 artifact truth layer
- live local relay `/v1/files` roundtrip
- live VPS relay `/v1/files` roundtrip
- live deployment 下的 `22 MiB -> file_surface` owner-lane 样本
- live deployment 下的 `34 MiB -> cos` oversize 兼容样本
- 候选 `artifact_manifest` 的最小投影与 gap 记录

# `/v1/files` Authenticated Consumer Smoke 验证结果

## 目的

本文记录当前 `/v1/files` consumer/cutover 验证已经实际测到什么、证据在哪，以及这份结论当前不代表什么。

## 本次记录对应环境

- 日期：`2026-03-27`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- smoke 入口：`.\.venv\Scripts\python.exe .\scripts\file_surface_consumer_smoke.py`

## 已完成验证

已运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\file_surface_consumer_smoke.py --config .\mail_config.bot.relay.local.yaml --run-name file-surface-consumer-smoke-20260327_live_vps_authenticated_consumer
```

结果：

- 状态：成功
- smoke 类型：live relay-host authenticated consumer read
- owner-lane staging：成功
  说明：本次 consumer smoke 先复用同一 relay host `/v1/files` owner-lane staging，把 `artifact-preview` externalize 到 live file surface
- `artifact_manifest.download_ref_source`：`external_delivery_index.file_surface`
- authenticated `GET download_ref`：成功
  说明：携带当前 relay transport token 时，`GET download_ref` 返回 `200`，且回读字节与本地 `preview.png` 一致
- anonymous `GET download_ref`：拒绝
  说明：缺少 `Authorization` header 时返回 `401 unauthorized`
- wrong-token `GET download_ref`：拒绝
  说明：携带错误 Bearer token 时返回 `401 unauthorized`

## 当前已确认结论

1. 当前 `/v1/files` download URL 不是匿名公开 URL。
2. 当前 `artifact_manifest.download_ref_source=external_delivery_index.file_surface` 的 consumer 读法已经在 live relay host 上补齐正向证明。
3. 当前可成立的 consumer 结论是：拥有 current transport token 的 consumer 可以稳定消费 `download_ref`。
4. 因此，repo-side `/v1/files` cutover 当前不再隐含依赖 `COS`-specific download contract。

## 当前不代表什么

这轮 smoke 不应被误读成以下结论已经成立：

- `/v1/files` 已经是匿名公开下载面
- Android end-user app 已经整体切到 `/v1/files` 作为新的 general-purpose API
- Android app token 可以直接替代 internal transport token 访问 `/v1/files`
- `COS` 已经满足删除准备

## 证据

- `_tmp_file_surface_consumer_smoke/file-surface-consumer-smoke-20260327_live_vps_authenticated_consumer/smoke_result.json`
- `_tmp_file_surface_consumer_smoke/file-surface-consumer-smoke-20260327_live_vps_authenticated_consumer/artifact_contract_smoke/file-surface-consumer-smoke-20260327_live_vps_authenticated_consumer-artifact-contract/smoke_result.json`

## 当前建议读法

- 截至 `2026-03-27`，当前 `/v1/files` owner lane 与 transport-token-scoped consumer lane 都已经有 live evidence
- 因此，后续联调不必再把“consumer 还能不能读 `/v1/files`”当成 repo-side 主缺口
- 更合理的下一步是进入 Android-facing seam 联调，并继续保持“`window_ready` 是当前 gate，`cos_decommission_candidate` 不是当前前置”这条口径

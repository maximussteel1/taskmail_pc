# TaskMail `/v1/files` Repo-Side 实现责任说明（v0.1）

更新时间：2026-03-23

## 状态

- 本文是 `mail_based_task_manager` 仓对 vNext `/v1/files` file surface 的 repo-side 最小实现责任说明。
- 本文不改写 `docs/current/*` current truth；当前仓库已落地的 artifact truth 仍是 `RunArtifact` + `artifact_index.json`。
- 本文的作用是明确：repo-side 如何把本地 artifact truth 投影成 Android / relay transport 可消费的外部文件面。

## Read First

- `docs/plans/taskmail_android_pc_control_artifact_companion_note_v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-pc-control-artifact-contract-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-pc-communication-development-conditions-v0.1.md`
- `docs/plans/run_artifact_delivery_plan.md`
- `docs/plans/taskmail_artifact_fileid_mapping_sidecar_note_v0.1.md`
- `docs/plans/taskmail_transport_token_reconnect_upload_error_companion_note_v0.1.md`
- `docs/current/multimedia_mail_protocol.md`
- `mail_runner/artifact_resolver.py`
- `mail_runner/external_delivery.py`

## 1. 一句话责任

`/v1/files` 在 repo-side 的首版责任是：

- 接住来自 PC runtime 的 canonical 文件 bytes 与 metadata
- 生成稳定、可下载、可校验的 `file_id`
- 对 Android 暴露统一 metadata 与 content 读取面

它不是新的本地 artifact truth；本地真相仍在 PC 侧 `RunArtifact` / `artifact_index.json`。

## 2. 与 current artifact truth 的关系

repo-side 当前必须保持以下边界：

- `RunArtifact` / `artifact_index.json`
  - repo-local canonical artifact truth
- `/v1/files`
  - transport-facing external file surface

这意味着：

- Android 不应读取 PC 本地绝对路径
- `artifact_id` 是逻辑 artifact identity
- `file_id` 是 transport-facing file object identity
- 同一个 `artifact_id` 在不同上传轮次可映射到不同 `file_id`
- 同一个 `file_id` 必须始终指向同一份 bytes 与 metadata

repo-side 不应把 `/v1/files` 写成“本地路径字符串转发器”。

## 3. `/v1/files` 首版最小责任

### 3.1 统一认证

repo-side 首版必须：

- 让 `POST /v1/files`
- `GET /v1/files/{file_id}`
- `GET /v1/files/{file_id}/content`

都复用与 `/control` 相同的 `Authorization: Bearer <transport_token>` admission。

repo-side 首版不应：

- 引入第二套 file-only token
- 让 mailbox 凭据充当 file auth
- 让无认证 public URL 成为默认下载路径

### 3.2 `POST /v1/files` 责任

repo-side 首版 `POST /v1/files` 至少负责：

- 校验 transport token
- 接收单次上传的文件 bytes 与 metadata
- 校验 `byte_size`
- 重新计算并确认 `sha256`
- 生成稳定 `file_id`
- 返回 canonical artifact descriptor

repo-side 首版冻结边界：

- `single_file_upload_limit_bytes = 33554432`（32 MiB）
- 只承诺 single-shot upload
- 不承诺 chunk upload
- 不承诺 resume upload
- 不承诺 ranged upload

repo-side 最低失败语义：

- 超限返回 `413` 或语义等价的 machine-readable error
- metadata 非法返回明确的 4xx error
- hash / byte-size 不一致时返回明确拒绝，而不是 silent truncate

### 3.3 artifact descriptor 回填责任

repo-side `POST /v1/files` 成功后，返回体必须能回填 shared contract 上的 artifact descriptor。

最低字段：

- `artifact_id`
- `file_id`
- `name`
- `kind`
- `role`
- `mime_type`
- `byte_size`
- `sha256`
- `metadata_url`
- `download_url`

可选字段：

- `image`
- `inline_preview`

repo-side 责任不是“把上传收下就算完”，而是“把 transport-facing descriptor 补完整”。

### 3.4 `GET /v1/files/{file_id}` 责任

repo-side 首版 metadata endpoint 至少负责：

- 按 `file_id` 返回 JSON metadata
- 保证 `sha256`、`byte_size`、`mime_type` 与真实内容一致
- 返回与上传成功时一致的 `file_id`
- 返回可复用的 `metadata_url` 与 `download_url`

repo-side 不应：

- 根据临时本地路径重新猜 metadata
- 对同一 `file_id` 返回不同 bytes 对应的 metadata

### 3.5 `GET /v1/files/{file_id}/content` 责任

repo-side content endpoint 至少负责：

- 返回原始 bytes
- 返回正确 `Content-Type`
- 在可行时返回稳定 `Content-Length`
- 让调用方能用返回内容重新校验 `sha256`

repo-side 不应：

- 对同一 `file_id` 做内容替换
- 为了预览方便偷偷返回降质内容替代原文件

### 3.6 小图片与 `inline_preview`

repo-side 对小图片的责任是“补预览”，不是“让 JSON 变成图片主通道”。

冻结读法：

- 正式图片真相仍是 `file_id + download_url`
- `inline_preview` 只用于 preview
- `inline_preview.byte_size <= 65536`
- 即使没有 `inline_preview`，`download_url` 也必须可用

## 4. repo-side file store 基线

repo-side 首版并不要求立即接入复杂对象存储，但必须满足文件面最低耐久性：

- 文件 bytes 可持久化
- metadata 可持久化
- `file_id -> bytes + metadata` 可稳定读取
- 写入失败不会留下“metadata 成功、bytes 缺失”的半成品真相

repo-side 当前允许：

- 先使用本地 file-backed store
- 后续再替换成对象存储或更正式 file service

repo-side 当前不允许：

- 只在进程内存里暂存 `file_id`
- 让 `download_url` 指向会随 PC 本地路径漂移而失效的临时文件位置

## 5. `artifact_id -> file_id` 映射责任

repo-side 必须明确区分两层映射：

1. 本地运行层
   - `artifact_id -> RunArtifact`
2. transport 文件层
   - `artifact_id -> file_id`

repo-side uploader 的最低责任：

- 从 `RunArtifact` / `artifact_index.json` 读出 canonical metadata
- 在上传成功后把 `artifact_id -> file_id` 映射回填到 control payload、result、event 或 sidecar
- 不把 `file_id` 倒写成新的 repo-local artifact truth 主键

repo-side file service 的最低责任：

- 保证 `file_id` 稳定
- 保证同一 `file_id` 指向同一份 bytes
- 若按 `sha256` 做去重，也必须保持 `file_id -> bytes` 不变

## 6. 与当前仓库资产的关系

repo-side 当前可复用的资产：

- `docs/plans/run_artifact_delivery_plan.md`
  - 已冻结本地 `RunArtifact` / `artifact_index.json` 分层
- `mail_runner/artifact_resolver.py`
  - 已有 repo-local artifact 解析起点
- `mail_runner/external_delivery.py`
  - 已有 repo-side 上传、URL 回填、`sha256` 相关经验
- `docs/current/multimedia_mail_protocol.md`
  - 已有大文件与外部投递经验边界

vNext `/v1/files` 不应把这些资产推翻重写；更合理的路径是：

- 保留本地 artifact truth
- 把已有上传/回填经验收口为统一 file surface

## 7. Merge Gate

在把 `/v1/files` 接进 Android shared flow 之前，repo-side 至少应具备以下证据：

1. `POST /v1/files` 成功样本已闭环
2. `GET /v1/files/{file_id}` 与 `GET /v1/files/{file_id}/content` 已闭环
3. 下载内容的 `sha256` 与 metadata 一致
4. 超限上传已返回明确 `413` 或等价 machine-readable error
5. current `RunArtifact` / `artifact_index.json` truth 没被 `/v1/files` 反向污染

## 8. 当前结论

repo-side 对 `/v1/files` 的首版责任已经够清楚：

- 与 `/control` 统一认证
- 单次上传
- 稳定 `file_id`
- metadata / content 双读取面
- 不替代本地 artifact truth

接下来的重点不是继续争论“文件是不是独立面”，而是把 `artifact_id -> file_id` 回填与 file store durability 补成最小可验证实现。

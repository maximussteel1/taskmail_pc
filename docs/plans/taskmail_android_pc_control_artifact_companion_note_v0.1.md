# TaskMail Android-PC 控制面与文件面合同 Companion Note（v0.1）

更新时间：2026-03-23

## 状态

- 本文是 `mail_based_task_manager` 仓对 Android-side shared contract 的 repository-side companion note。
- 本文不改写当前 `docs/current/*` truth。
- 本文的作用是：明确仓库侧承认的共享 transport shell、开发前置条件、已冻结的 repo-side 基线，以及仍属二级细节的后续 companion gap。

## Read First

- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-pc-control-artifact-contract-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-pc-communication-development-conditions-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-transport-probe-payload-contract-v0.1.md`
- `docs/plans/taskmail_transport_probe_payload_companion_note_v0.1.md`
- `docs/current/android_runner_communication_contract.md`
- `docs/platform/relay_transport_protocol_draft.md`
- `docs/plans/run_artifact_delivery_plan.md`
- `docs/plans/android_pc_vps_evolution_authority.md`
- `docs/plans/taskmail_control_plane_repo_responsibility_note_v0.1.md`
- `docs/plans/taskmail_file_surface_repo_responsibility_note_v0.1.md`
- `docs/plans/taskmail_artifact_fileid_mapping_sidecar_note_v0.1.md`
- `docs/plans/taskmail_relay_accepted_result_replay_evidence_note_v0.1.md`
- `docs/plans/taskmail_transport_token_reconnect_upload_error_companion_note_v0.1.md`

## 1. 仓库侧承认什么

对于下一轮 Android / PC / VPS unified control plane，本仓库侧承认以下 shared boundary 可以作为实现前提：

- 文本与控制面：`WebSocket + JSON envelope`
- 文件与二进制面：`HTTP upload/download + JSON metadata reference`
- shared control endpoint：`/control`
- shared file endpoints：
  - `POST /v1/files`
  - `GET /v1/files/{file_id}`
  - `GET /v1/files/{file_id}/content`
- shared base ids：
  - `trace_id`
  - `probe_id`
  - `request_id`
  - `packet_id`
  - `receipt_id`
  - `result_id`
  - `subscription_id`
  - `workspace_id`
  - `session_id`
  - `message_id`
  - `source_id`
  - `artifact_id`
  - `file_id`

本 note 的承认含义是：

- 这些边界可以作为仓库侧后续实现与 companion docs 的 planning baseline
- 它不等于这些行为今天已经是 `docs/current/*` 描述的已实现事实

仓库侧同时承认：

- shared control frame 应携带 `trace` 语义，而不是只靠 `request_id` / `packet_id`
- `command` / `event` / `result` 应保留统一的 `related` identity block

## 2. 当前不改什么

本 note 不改变以下 current-truth 读法：

- Android 当前仍以 `docs/current/android_runner_communication_contract.md` 所描述的 mail-first contract 为 current behavior
- 当前 mail attachment / reporter / `artifact_index.json` 仍然是仓库侧已落地 artifact truth 基线
- 当前 `new_task`、`reply/status`、`[SYNC]` 的 Layer 1 行为不因本 note 自动切换

## 3. 仓库侧同意的 transport shell

### 3.1 控制面

仓库侧同意后续 vNext control plane 统一围绕以下 message set 演进：

- `hello`
- `hello_ack`
- `command`
- `command_ack`
- `event`
- `result`
- `error`
- `ping`
- `pong`

### 3.2 文件面

仓库侧同意后续 artifact/file 通道统一围绕以下 endpoint 演进：

- `POST /v1/files`
- `GET /v1/files/{file_id}`
- `GET /v1/files/{file_id}/content`

### 3.3 vNext endpoint 与 current endpoint 的关系

仓库侧当前读法必须分开：

- current Layer 1 truth 仍是 `docs/current/*` 上的 `/relay`
- 本 note 承认的是 vNext shared target shell：`/control` + `/v1/files`
- 在 cutover 前，repo-side 可以通过 adapter / alias / compatibility seam 复用已有 `/relay` 资产
- 但不应把 `/relay` 与 `/control` 长期保留成两份并列的 Android-facing shared shell

### 3.4 小图片规则

仓库侧同意 Android 文档里的以下边界：

- 图片 canonical truth 仍是 `file_id + HTTP content`
- JSON 可选携带小图 `inline_preview`
- `inline_preview` 只是预览，不是正式图片真相

## 4. 仓库侧同意的相关性键

仓库侧同意在 projector / event bind / probe 时间线里，不再只靠 `request_id` 和 `packet_id`。

后续 shared set 至少包括：

- `trace_id`
- `probe_id`
- `request_id`
- `packet_id`
- `receipt_id`
- `result_id`
- `workspace_id`
- `session_id`
- `message_id`
- `source_id`
- `artifact_id`
- `file_id`
- `subscription_id`

仓库侧同意的解释是：

- `workspace_id` / `session_id` 是 read-side bind 一等键
- `request_id` / `packet_id` / `receipt_id` / `result_id` 继续是 outbound、accepted 与 final-result replay 的主锚点
- `message_id` 只表示 mail artifact identity
- `source_id` 表示外部 source fact identity，不等于 `message_id`
- `artifact_id` 与 `file_id` 需要显式区分
- `trace_id` 是跨 hop 观测与调试锚点
- `probe_id` 是 harness/debug 场景的一等锚点
- `subscription_id` 只用于持续事件流，不替代 `request_id`

## 5. 仓库侧同意的开发条件

仓库侧接受以下开发顺序作为合理前提：

1. 先冻结 shared transport shell
2. 先冻结 `transport_probe` payload schema
3. 先打通 `/control` 与 `/v1/files`
4. 先让 harness/observability 落地
5. 再切统一业务 payload

也就是说，仓库侧不要求等 Android 大 cutover 完成后才允许先做 harness。

同时，仓库侧同意 probe timeline 不能只靠 `recorded_at`：

- probe 事件应额外带 `clock_source`
- probe 事件应额外带单机可比较的 `monotonic_ms`
- 如果有可信校时，还可带 `clock_offset_ms`

仓库侧当前承认这套时间语义是合理要求，而不是 Android 侧的可选 embellishment。

## 6. 仓库侧冻结的 repo-side 基线

以下项目在本 note 中作为 repo-side planning baseline 先冻结；它们仍不等于 current behavior 已落地，但后续 companion docs 与实现计划应默认复用这些决定。

### 6.1 `single_file_upload_limit_bytes`

- 仓库侧第一版承诺值：`single_file_upload_limit_bytes = 33554432`（32 MiB）
- 选择这个值的 repo-side 原因是：它与当前 relay runtime / tests 已使用的 `32 * 1024 * 1024` WebSocket message ceiling 保持同量级，避免首版 `/control` 与 `/v1/files` 出现明显更小的隐式截断边界
- 第一版仍只承诺 single-shot upload，不承诺 chunk、resume 或 ranged upload
- 超限时 `/v1/files` 应显式返回 `413` 或语义等价的 machine-readable error
- `inline_preview_max_bytes = 65536` 与 `json_text_field_soft_limit = 65536` 继续跟随 shared Android doc，不在本 note 另起 repo-side 数值分叉

### 6.2 artifact hosting 责任归属

- repo-side 冻结读法：对 Android 暴露的外部文件真相由 relay/file service 承担，而不是由 PC 本地路径直接承担
- PC runtime 的职责是：
  - 从本地 `RunArtifact` / `artifact_index.json` 产出 canonical 文件 bytes 与 metadata
  - 作为 uploader 把文件提交到 `/v1/files`
  - 在 control payload / result / event 中保留 `artifact_id` 与 `file_id` 的映射
- relay/file service 的职责是：
  - 分配并返回 `file_id`
  - 持久化文件 bytes 与 metadata
  - 生成 `download_url` 或等价下载定位
  - 对 Android 提供 `GET /v1/files/{file_id}` 与 `GET /v1/files/{file_id}/content`
- Android 不应读取 PC 本地路径；`E:\...` 绝对路径不属于 shared file contract
- 当前本仓的 `RunArtifact` / `artifact_index.json` 仍是 repo-local artifact truth；`/v1/files` 是外部 transport-facing file surface，不替代本地真相层

### 6.3 accepted/replay 兜底方

- repo-side 冻结读法：一旦 `/control` 返回 `accepted = true`，对 Android 可见的 replay authority 由 relay-side durable store 兜底
- PC runtime 仍然是业务执行与业务结果的真相来源，但它不是 accepted-path 对外 replay identity 的第一响应方
- 这意味着：
  - relay-side 至少要能稳定重放同一 `receipt_id`
  - 若已有 final result，relay-side 至少要能稳定重放同一 `result_id`
  - 如果 relay-side 尚未具备 accepted 后 replay 能力，就不应把该请求提升为 `accepted = true`
- 对于 repo-side 第一版，PC runtime 可以负责 materialize result，relay-side 负责 durable 缓存并对 Android 重放
- accepted 前失败仍可按 payload-specific 规则分类为 `fallbackable` 或 `terminal`；accepted 后连接丢失则先 replay，不做 silent mail fallback

### 6.4 `/control` 与 `/v1/files` 的 auth 路径

- repo-side 第一版承诺就是统一 `Authorization: Bearer <transport_token>`
- WebSocket upgrade 与 HTTP file endpoint 应复用同一 transport token verifier，而不是拆成两套凭据体系
- `hello` / `hello_ack` 仍然保留；它们负责：
  - client identity
  - supported payload schema negotiation
  - heartbeat negotiation
  - `transport_token_id` 级别的 operator-facing 诊断
- mailbox 凭据不得拿来认证 `/control` 或 `/v1/files`
- query 参数 token、HTML form token、临时第二套 file token 都不应成为第一版 repo-side 规划基线
- transport token 在 repo-side 当前仍是 narrow, operator-provisioned capability；本 note 不把它升级成通用 Android app credential

### 6.5 repo-side 首批 payload / capability 基线

仓库侧接受以下顺序作为首批 capability baseline：

1. `transport_probe`
2. `taskmail-bootstrap-control-contract-v2`
3. 现有已冻结的 post-creation session action 相关 direct contract

仓库侧不建议在 `/control` 与 `/v1/files` 的第一刀里同时放入：

- 通用附件平台语义
- 全量 history API
- 未冻结 schema 的 reply/control 新业务 payload

## 7. 与现有 artifact 体系的关系

本 note 不要求废弃当前 `RunArtifact` / `artifact_index.json`。

当前合理读法是：

- `RunArtifact` / `artifact_index.json` 仍是仓库侧本地 artifact truth
- `/v1/files` 是面向 Android / relay transport 的对外文件投递面
- 二者后续应建立稳定映射，而不是彼此替代
- 推荐映射读法：
  - `artifact_id` = repo-local logical artifact identity
  - `file_id` = transport-facing file object identity
  - 同一个 `artifact_id` 在不同上传轮次可映射到不同 `file_id`
  - 同一个 `file_id` 必须始终指向同一份 bytes 与 metadata

## 8. 下一步建议

仓库侧最合理的下一步不是立刻写业务 payload，而是：

1. 按 `/control`、`/v1/files` 与 token/reconnect/upload-error companion notes 开始补首版接口骨架
2. 给 future payload family 的 generic result continuity 补测试与 evidence note
3. 把 probe / bootstrap / file upload 的错误回填继续收口到统一 sidecar 与 evidence 产物

## 9. 当前结论

本仓库已经可以在 planning 层正式承认：

- Android-side shared control/file contract 是合理的
- Android-side development conditions 是合理的
- harness 应先于大业务 cutover 落地
- upload size、artifact hosting、accepted-path replay ownership、`/control`/`/v1/files` auth 路径已经在 planning 层有了 repo-side baseline

但当前还不应误读成：

- 这些能力已经是 current behavior
- repo-side owner 已经对全部实现细节签字
- token 轮换细则、file backend 物理实现、chunk/resume upload 等二级细节已经在本 note 冻结

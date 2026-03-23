# TaskMail transport token / reconnect / 上传错误 Companion Note（v0.1）

更新时间：2026-03-23

## 状态

- 本文是 `mail_based_task_manager` 仓对 transport token、重连边界与 `/v1/files` 上传错误形状的 repository-side companion note。
- 本文不改写 `docs/current/*` current truth。
- 本文的作用是把较大的 Phase 5 token/reconnect 讨论，压成 Android-PC unified control/file planning 现在就能执行的 repo-side 基线。

## Read First

- `docs/plans/taskmail_android_pc_control_artifact_companion_note_v0.1.md`
- `docs/plans/taskmail_control_plane_repo_responsibility_note_v0.1.md`
- `docs/plans/taskmail_file_surface_repo_responsibility_note_v0.1.md`
- `docs/plans/taskmail_artifact_fileid_mapping_sidecar_note_v0.1.md`
- `docs/plans/taskmail_relay_accepted_result_replay_evidence_note_v0.1.md`
- `docs/plans/phase5_token_and_reconnect_handling_note.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-pc-control-artifact-contract-v0.1.md`
- `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-android-pc-communication-development-conditions-v0.1.md`
- `docs/current/android_runner_communication_contract.md`
- `docs/current/multimedia_mail_protocol.md`
- `mail_runner/relay_server/app.py`
- `mail_runner/relay_server/loopback.py`
- `tests/test_outbound_relay_bootstrap.py`
- `tests/test_relay_server_runtime.py`

## 1. 一句话结论

repo-side 第一版冻结读法是：

- `/control` 与 `/v1/files` 共用同一 `Authorization: Bearer <transport_token>`；
- token 轮换是 operator out-of-band 行为，不引入 app-side refresh API；
- 重连的单位是“重开连接并重新 `hello`”，不是“沿旧连接续命”；
- 一旦 `accepted = true`，逻辑请求连续性继续由同一组 `request_id + packet_id` 承担，而不是由 token 承担；
- `/v1/files` 上传失败必须返回 machine-readable JSON error，不允许 silent truncate、silent fallback 或半成功 descriptor。

## 2. 本文不改什么

本文不把以下内容改写成 current fact：

- current Android-facing direct boundary 仍是 `docs/current/android_runner_communication_contract.md` 里的 `/relay`
- current repo 并没有现成 `/v1/files` 实现
- 本文不引入 chunk upload、resume upload、range download
- 本文不引入 `refresh_token`、`/control/token`、`refresh_transport_token` 之类的新接口
- 本文不把 transport token 升级成通用 Android app credential

## 3. repo-side transport token 基线

### 3.1 capability 边界

repo-side 第一版继续冻结以下边界：

- transport token 是 narrow、operator-provisioned capability
- `/control` 与 `/v1/files` 必须复用同一套 token verifier
- mailbox 凭据、query 参数 token、HTML form token、第二套 file-only token 都不属于首版基线
- token 的 operator-facing 指纹继续通过 `transport_token_id` 暴露给诊断层，而不是业务层

### 3.2 expiry / rotation 冻结读法

repo-side 当前冻结的不是“具体轮换平台怎么做”，而是“客户端能依赖什么”：

- token 轮换由 operator out-of-band 下发与切换
- shared contract 首版不要求客户端依赖 dual-token overlap window
- server 侧即使为了部署方便短暂接受 old/new 两个 token，这也只是实现细节，不是客户端可依赖合同
- 对客户端来说，任一时点只有“当前被 provision 的最新 token”可作为新连接 authority
- token 切换不会改变 `request_id`、`packet_id`、`receipt_id`、`result_id` 的语义

这意味着：

- 不能因为 token 轮换就新建一套逻辑请求 identity
- 不能把 token 更新误读成“fresh user tap”
- 不能让 upload/download/control 三条路径各自维护不同 token 生命周期

### 3.3 bootstrap admission 与 token 错误分类

repo-side 当前已经有足够明确的 admission vocabulary，后续 `/control` 继续沿用这层读法：

- `unauthorized`
  - Bearer token 无效或缺失
- `token_id_mismatch`
  - `hello.transport_token_id` 与当前 token fingerprint 不一致
- `hello_ack`
  - 当前连接已通过 admission，可进入后续 control/request 语义

冻结解释：

- `unauthorized` 或 `token_id_mismatch` 出现在 `hello` 阶段时，属于 bootstrap admission failure
- 这类 failure 表示 direct path 当前不可用或 token 过期，不表示业务请求已进入 accepted lane
- 业务 packet 上的 `unauthorized` 仍应按 payload contract 读成 hard rejection 或 protocol rejection，不与 bootstrap token 过期混为一谈

## 4. repo-side reconnect / replay 基线

### 4.1 reconnect 的最小动作

repo-side 第一版把 reconnect 冻结为以下序列：

1. 建立一个新的 WebSocket 连接
2. 使用当前 provision 的 `Authorization: Bearer <transport_token>`
3. 重新发送 `hello`
4. 等待新的 `hello_ack`
5. 若之前已有逻辑请求，则按原 `request_id + packet_id` 走 replay 或续传逻辑

首版不承诺：

- 在旧连接上原地 refresh token
- 不经 `hello_ack` 就直接恢复 business stream
- 单靠 TCP reconnect 自动恢复 accepted-path continuity

### 4.2 accepted 前后的分界

repo-side 第一版继续保持以下边界：

- `hello_ack` 前
  - 还没有 accepted lane
  - 连接失败、超时、`unauthorized`、`token_id_mismatch` 都属于 bootstrap/admission failure
- `hello_ack` 后、`accepted = true` 前
  - 仍可视为同一次 relay-level retry
  - 如果还没有向用户暴露 fresh failure，则可以复用同一 `request_id + packet_id`
- `accepted = true` 后
  - 必须先用当前 token 完成 reconnect/replay
  - 继续复用同一 `request_id + packet_id`
  - 不允许 silent mail fallback

这与 current Android contract 保持一致：accepted-path 的第一反应是 replay 同一逻辑请求，而不是改写 identity。

### 4.3 token 轮换下的 replay 解释

token 轮换发生时，repo-side 需要把“认证连续性”和“请求连续性”拆开：

- 认证连续性
  - 由新的 token + 新的 `hello_ack` 重新建立
- 请求连续性
  - 由已有 `request_id + packet_id` 延续
- replay authority
  - 继续由 relay-side durable store 负责，不因为 token 更新而迁回 PC 内存态

因此首版不允许：

- 因为 token 更新而给已 accepted 的请求重新发新 `receipt_id`
- 因为重连而把同一 final result 变成新的 `result_id`
- 因为 token 切换失败就把 accepted-path 静默降级成 mail fallback

## 5. `/v1/files` 上传错误形状基线

### 5.1 response shape

repo-side 第一版冻结 `/v1/files` 失败返回至少满足：

- HTTP status 明确
- `Content-Type: application/json; charset=utf-8`
- body 为 machine-readable JSON

推荐最小形状：

```json
{
  "status": "error",
  "error_code": "payload_too_large",
  "error_message": "single_file_upload_limit_bytes exceeded",
  "retryable": false,
  "trace_id": "trace_01JQ...",
  "artifact_id": "artifact_chart",
  "max_bytes": 33554432,
  "observed_bytes": 41234567
}
```

最小必需字段：

- `status`
- `error_code`
- `error_message`
- `retryable`

强烈建议字段：

- `trace_id`
- `artifact_id`
- `max_bytes`
- `observed_bytes`
- `expected_sha256`
- `received_sha256`

### 5.2 error code 与 HTTP status 基线

repo-side 第一版最少冻结以下错误：

| HTTP status | error_code | 语义 | retryable |
| --- | --- | --- | --- |
| `401` | `unauthorized` | Bearer token 缺失、错误或已失效 | `false` |
| `413` | `payload_too_large` | 超过 `single_file_upload_limit_bytes = 33554432` | `false` |
| `400` | `invalid_metadata` | 必需 metadata 缺失、非法或不自洽 | `false` |
| `409` | `hash_mismatch` | 客户端声明的 `sha256` 与服务端实收不一致 | `false` |
| `409` | `byte_size_mismatch` | 客户端声明的 `byte_size` 与服务端实收不一致 | `false` |
| `503` | `storage_unavailable` | 后端存储暂不可用 | `true` |
| `500` | `store_write_failed` | 存储写入失败或 descriptor 落盘失败 | `true` |

repo-side 首版不允许：

- 返回纯文本错误而没有 machine-readable code
- 返回 `200` 但内部 silently truncate
- 分配了 `file_id` 却没有 durable bytes / metadata
- 失败后偷偷退回 mail attachment 路径冒充成功上传

### 5.3 descriptor 与失败边界

repo-side 第一版要把“成功拿到 descriptor”和“请求已经发出去”严格分开：

- 只有在 bytes 与 metadata 都 durable 后，才允许返回成功 descriptor
- 成功 descriptor 一旦返回，`file_id` 必须可稳定 `GET metadata` 与 `GET content`
- 如果请求失败，则不得返回半成品 `file_id`
- 如果客户端没有拿到成功 descriptor，就不得把本次上传写成可复用 binding truth

## 6. 上传重试与 sidecar 回填边界

repo-side 第一版冻结以下读法：

- `/v1/files` 首版只有 whole-request retry，没有 chunk/resume
- 上传中断、连接断开或 token 轮换后，客户端只能重新发起完整 `POST /v1/files`
- `artifact_id` 仍保持同一 repo-local identity
- 只有成功 response 才能把 `artifact_id -> file_id` 写入 sidecar 为 `uploaded`
- 失败 response 也应写 sidecar，但状态是 `failed`，并保留 `error_code` / `error_message`

这意味着：

- sidecar 不能记录“看起来像成功”的暂存 `file_id`
- 不能在失败后只靠 console log 记账
- 即使服务端未来支持按 `sha256` 去重，客户端也不能在没有成功 descriptor 的情况下假设已有可复用 `file_id`

## 7. 当前仓库证据基线

本文不是空口冻结，repo-side 当前已经有以下证据可复用：

- `mail_runner/relay_server/app.py`
  - 已有 `Authorization: Bearer <transport_token>` 提取逻辑
- `mail_runner/relay_server/loopback.py`
  - 已区分 `unauthorized` 与 `token_id_mismatch`
  - 已把 `hello_ack` 作为 admission 成功信号
- `tests/test_outbound_relay_bootstrap.py`
  - 已验证有效 token 获得 `hello_ack`
  - 已验证错误 token 返回 `unauthorized / transport token mismatch`
- `tests/test_relay_server_runtime.py`
  - 已验证 runtime WebSocket path 走 Bearer auth + `hello`
- `docs/current/android_runner_communication_contract.md`
  - 已冻结 `accepted = true` 后优先 replay 同一 `packet_id + request_id`
- `docs/current/multimedia_mail_protocol.md`
  - 已冻结“外部文件投递失败时必须显式报错，而不是静默回退成另一条成功路径”

## 8. 下一步建议

在这个 note 之后，repo-side 最合理的下一步是：

1. 把 `/control` 的 auth / reconnect 路径按本文冻结语义接到实现骨架。
2. 把 `/v1/files` 的 JSON error shape 与 sidecar `failed` binding 一起落地。
3. 给 future payload family 的 generic result continuity 继续补测试与 evidence。

## 9. 当前结论

repo-side 现在已经不需要继续模糊地说“后面再看 token 轮换和上传错误”。

当前可以明确冻结的基线是：

- token 是单一路径、operator-provisioned、out-of-band rotation 的 transport capability
- reconnect 是“新连接 + 新 `hello_ack` + 旧逻辑请求 identity”
- accepted-path continuity 仍归 `request_id + packet_id + durable replay authority`
- `/v1/files` 失败必须有 machine-readable JSON error，且与 sidecar failure binding 对齐

当前仍未冻结的，不是这些 repo-side 边界，而是更后面的 generic payload/result family 细节。

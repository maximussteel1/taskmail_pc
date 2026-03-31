# Android Session Updates Facade Contract

> 文档层级：Layer 1（current app-facing read contract）
>
> 当前路径：`docs/current/android_session_updates_facade_contract.md`

## 状态

- 日期：2026-03-29
- 目的：冻结当前 `WS /v1/android/session-updates` 的已实现行为
- 范围：鉴权、query locator、message envelope、推送语义、错误面

## 1. 一句话契约

当前 repo-side 已提供 Android-facing 的实时 detail 更新通道：

- `WS /v1/android/session-updates`

它是 `GET /v1/android/session-snapshot` 的同构 push 版本，服务于当前 detail 页刷新。

## 2. 鉴权

当前固定使用：

- `Authorization: Bearer <android_app_token>`

如果 token 不匹配，服务器会先发送 error envelope，再关闭连接。

## 3. Query Locator

当前 query locator 与 `GET /v1/android/session-snapshot` 保持一致，支持：

- `workspace_id`
- `repo_path`
- `workdir`
- `session_id`
- `thread_id`

locator 解析规则、歧义处理和 supporting identity 校验，与 `session-snapshot` 完全同构。

## 4. Message Envelope

当前服务端只主动发送两类 message：

- `message_type = session_snapshot`
- `message_type = error`

顶层 envelope 固定包含：

- `schema_version`
- `subscription_id`
- `message_type`
- `sent_at`
- `payload`

当前 `schema_version` 固定为：

- `taskmail-android-session-updates-v1`

## 5. `session_snapshot` Message

当 `message_type = session_snapshot` 时：

- `payload` 与 `GET /v1/android/session-snapshot` 的成功返回 payload 同构

也就是说，Android 可以复用同一套 snapshot 解析模型消费：

- HTTP 点查结果
- WS push 结果

## 6. Push 语义

当前服务端行为固定为：

1. 连接建立并通过鉴权后，先推送一次当前完整 snapshot
2. 之后只在业务内容变化时再次推送

当前“业务内容变化”的判定范围是：

- `locator`
- `session`
- `session_snapshot`

因此：

- 单纯的 server 轮询 tick 不会重复推送
- 但 session 当前态、question state、history rounds、latest session action continuity，以及 `session_snapshot.live_process` 的变化都会触发新推送
- 单纯的 `snapshot_id / generated_at / sent_at` 更新时间不会单独触发重复 push

当前客户端不需要先发送 subscribe message。

## 7. 重连与补偿

当前 `session-updates` 没有客户端 ACK / 游标协商：

- 服务端不会因为客户端确认某条 snapshot 而停止后续 push
- 服务端也不会为同一连接维护“漏了哪一帧”的补发协议

当前 Android detail lane 的恢复语义固定为：

1. detail 页进入前台或首次打开时，连接 `WS /v1/android/session-updates`
2. 连接成功后先消费首帧完整 snapshot
3. 若连接断开、应用前后台切换，或客户端怀疑漏掉了中间 push：
   - 重新连接 `session-updates`
   - 必要时用同一 locator 重新调用 `GET /v1/android/session-snapshot` 做当前态补偿
4. 重新连上后，仍以新的首帧完整 snapshot 作为 authoritative 当前态

当前 Android 主链不应再把以下路径当作 detail 恢复入口：

- `/relay subscribe_session_detail`
- mail detail 通道
- 其他 legacy side lane

## 8. Error 语义

当 locator 非法、session 无法解析或 projection store 不可用时：

- 服务端发送一次 `message_type = error`
- `payload` 形态与 facade HTTP error payload 同构
- 随后关闭连接

当前 close 行为补充为：

- `close reason` 固定等于 `error_code`
- 当前常见 client/input/identity 错误关闭码是 `1008`
- `task_root_unavailable` 当前关闭码是 `1011`，它在这里表示 projection store 不可用的兼容错误码
- Android 应以 `error` envelope 内的 `error_code + retryable` 为主做恢复判断，不要只依赖 WebSocket close code

当前常见错误包括：

- `unauthorized`
- `task_root_unavailable`
- `invalid_payload`
- `session_not_found`
- `session_binding_unresolved`
- `workspace_identity_mismatch`
- `session_identity_mismatch`

## 9. 当前边界

当前 `session-updates` 不负责：

- 通用 Android command channel
- history-only 独立推送
- raw `output_chunk` transcript
- 双向 client message 协议

它当前只承担 Android detail 页的 authoritative snapshot push。

补充边界：

- 当前 push payload 里的 `session_snapshot.live_process` 是聚合后的 Android-facing live process
- 它和 `GET /v1/android/session-snapshot` 保持同构
- 它不是 raw chunk 列表，也不引入新的 client ACK / cursor 协议

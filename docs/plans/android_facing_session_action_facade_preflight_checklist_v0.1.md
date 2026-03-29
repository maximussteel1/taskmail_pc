# Android-Facing Session Action Facade 编码前置清单（v0.1）

## 状态

- 日期：2026-03-29
- 范围：repo-side 在开始 Android-facing `session-action` facade 编码前，必须先冻结的前置项
- 层级：planning / execution note
- 相关文档：
  - `docs/plans/android_facing_session_action_facade_requirements_v0.1.md`
  - `docs/plans/android_facing_create_session_facade_requirements_v0.1.md`
  - `docs/plans/post_creation_session_action_contract_v1.md`
  - `docs/current/android_session_snapshot_facade_contract.md`
  - `docs/current/android_runner_communication_contract.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-vps-first-multi-pc-session-action-facade-android-plan-v0.1.md`

## 目的

这份清单只回答一件事：

- repo-side 如果要把 `POST /v1/android/session-action` 真正推进到可编码状态，哪些前置冻结必须先完成

它不是新的 Layer 1 当前事实，也不是旧 direct `/control` contract 的替代正文。

## 当前读法

截至 `2026-03-29`，这条线应明确分三层读：

1. `docs/current/*` 仍描述当前事实：
   - Android 仍是 mail-first
   - repo-side 已有 Android-facing `session-action` first slice，当前覆盖 `reply/status/pause/resume/kill/end/answers/attachment_continuation`
   - `session-snapshot` 当前已经把 first-slice `command_id` continuity 与 `session_id` primary lookup 升成 Layer 1 contract
2. `post_creation_session_action_contract_v1.md` 仍只对应旧 direct `/relay` `/control` compatibility lane：
   - transport token admission
   - `hello / hello_ack`
   - `current_session` only
   - v1 只覆盖 `reply/status`
3. `android_facing_session_action_facade_requirements_v0.1.md` 描述的是新的 owner target：
   - Android-facing app API family
   - `android_app_token`
   - `session-action` 首发即承接全部用户可见 post-creation action
   - read-after-write 真相层回到 `session-snapshot / VPS-native projection`

因此，本轮真正缺的不是“再写一份愿景文档”，而是把新 facade 和旧 direct contract 之间的冻结差异收成单一、可执行的前置清单。

## 最新进度（2026-03-29）

repo-side 当前已经补到两个明确切片：

- repo-internal `pc_control` current-session `reply/status/pause/resume/kill/end/answers/attachment_continuation` command path 已有代码实现
- 当前 `command_dispatch(reply|status|pause|resume|kill|end|answers|attachment_continuation)` 会在 PC 本地复用现有 post-creation mail path
- 当前 result 侧会回投 `result.structured_payload.kind=session_action_result`
- Android-facing `POST /v1/android/session-action` 已扩到同一 current-session session-action first slice
- 当前 facade submit success/replay 会返回 `command_id + submit_ack + target_session_identity`
- 当前 same-`request_id` + same canonical payload replay，会稳定复用同一 `command_id`
- 当前 first `accepted` / first `accepted_but_queued` / same-payload replay，repo-side 都固定返回 `HTTP 200`
- 当前 same-`request_id` + different canonical payload，repo-side 固定返回 `HTTP 409 request_id_conflict`
- 当前 `create-session` 已要求显式 `canonical_reply_recipient`，并会把它写入 durable session binding
- 当前 `session-action` recipient resolve 已先读 durable `canonical_reply_recipient`，legacy mail-born session 才 fallback 到历史 inbound mail sender；若两者都缺失，当前 rejected code 固定为 `session_recipient_unresolved`
- current-session route authority 已切到 `session_id` primary target，`target.workspace_id / target.thread_id` 都降为 supporting identity
- `session-snapshot` 当前也已开始暴露 `latest_session_action.command_id` continuity，用于 `reply/status/pause/resume/kill/end/answers/attachment_continuation` tranche 的 read-after-write join

这一步的意义只有一个：

- 先把 canonical command-family seam 从“纯文档要求”推进到“已有可运行的 internal + facade first slice”

但它没有消掉本清单的主阻塞。以下判断保持不变：

- 这还不是全 action family hard-cut
- shared appendix、canonical action mapping、`command_id` continuity、以及长期 facade HTTP / replay surface 仍然没有冻结
- `session_id only` routing 虽然已经进入 current truth，但 shared appendix、canonical action mapping 和长期 continuity surface 仍然没有冻结

## 与旧 direct contract 的关键差异

相对于 `post_creation_session_action_contract_v1.md`，这次 facade 方案的变化不是局部改字眼，而是 owner seam 级别切换：

1. admission 从 `transport_token + websocket hello` 切到 `android_app_token + HTTP facade`
2. target scope 从 `current_session reply/status only` 扩到全部用户可见 post-creation action family
3. route authority 从 `workspace_id + session_id` 主路由，切到 `session_id` primary target、服务端回收 canonical resolve
4. continuity 主锚点从 `request_id` closeout 为主，切到 `command_id` 为主、`request_id` 为辅
5. owner truth 从 bridge-to-mail closeout，切到 `session-snapshot / VPS-native projection`
6. 公开错误面必须去掉 `/control hello_ack`、`direct_temporarily_unavailable` 这类 transport-era 语义

这意味着：旧 contract 与旧 execution plan 可以继续作为实现素材参考，但不能直接当作这次 facade 编码输入。

## 编码前必须先冻结的 6 项

### 1. Shared 请求/回包 appendix

必须先把 facade 自己的最小协议正文冻出来，而不是只停留在 requirements 级描述。

最少要覆盖：

- 顶层 envelope：
  - `request_id`
  - `action`
  - `target`
- success path：
  - `command_id`
  - `submit_ack`
  - `target_session_identity`
- 每个 action 的 action-specific body：
  - `reply`
  - `status`
  - `answers`
  - `pause`
  - `resume`
  - `kill`
  - `end`
  - `attachment_continuation`
- 字段级 required / optional / nullability
- 同一 action family 的合法最小样例与 reject 样例

完成定义：

- repo-side 与 Android-side 都能引用同一组字段口径，而不是各自从 requirements 文档里摘句子拼实现

### 2. `request_id` 幂等与冲突规则

当前 requirements 文档只说 `request_id` 是 client-side idempotency key，但还没有把行为冻结到可编码程度。

编码前必须明确：

- 同一 `request_id` + 同一规范化 payload 重放时：
  - 是否返回同一 `command_id`
  - 是否复用原始 `submit_ack`
  - 对外返回什么 HTTP status
- 同一 `request_id` + 不同 payload 时：
  - 对外返回什么 HTTP status
  - 对外返回什么 `error_code`
- 幂等窗口按什么范围生效：
  - 同一 `session_id`
  - 同一 `android_app_token`
  - 或全局
- fresh user tap 与 retry 的判定边界

建议读法：

- 对外尽量不要再新开一组 transport-era 式错误语义
- 如果需要暴露冲突，优先把它收进已有 facade 错误面，而不是重新把旧 direct 分类抬回主线

完成定义：

- repo-side 可以直接写 `request_id` store / dedupe 测试
- Android 可以直接决定什么时候复用旧 pending submission，什么时候创建新 pending submission

### 3. Server-side routing 与 snapshot locator 的衔接

新 facade 要求写路径以 `session_id` 为 primary target，但当前 `session-snapshot` 的 Layer 1 locator 仍不支持“只靠 `session_id` 读回”。

编码前必须先明确这两条 seam 如何衔接：

- submit 时，repo-side 最少返回什么 `target_session_identity`
- Android 后续读 `session-snapshot` 时，最小必需 locator 是什么
- `workspace_id` 与 `thread_id` 在 success response 里的职责：
  - 只作 diagnostics
  - 还是 Android 下一跳 read locator 的必需输入
- `session_binding_unresolved` 与 `session_not_found` 的边界

这里不冻结清楚，会直接导致：

- 写路径说“`session_id` 就够”
- 读路径却仍要求 Android 额外持有 `workspace_id` 或 `thread_id`
- pending submission 无法稳定进入 read-after-write continuity

完成定义：

- repo-side submit response 与 `session-snapshot` locator contract 之间不再有隐式跳步

### 4. Canonical command mapping 表

requirements 文档已经要求全部 action 进入 canonical `command_dispatch`，但当前还没有一张可编码的动作映射表。

编码前至少要冻结：

- 每个 facade action 对应的 canonical `command_type`
- action payload 到 command payload 的字段映射
- 哪些 action 允许 `accepted_but_queued`
- 哪些 action 只有 `accepted | rejected`
- `attachment_continuation` 的最小 payload 边界
- `answers` 与 `reply` 的语义分界

特别注意：

- 这一步不是把动作重新桥接回 mail body
- 也不是先允许“有的 action 继续 mail，后面再迁”

完成定义：

- repo-side 可以按 action family 写 handler / assembler / validator
- Android 可以按同一 action family 写 form serializer，而不是继续特判 reply/status

### 5. Projection / snapshot continuity appendix

这条是当前最大的实质缺口。

如果 `command_id` 是 owner continuity 主锚点，就必须先冻结它在 read truth layer 上如何可见。

编码前至少要明确：

- `session-snapshot` 或 projection 通过什么字段把最近 action 的 `command_id` 暴露给 Android
- Android 用什么字段判断“这条 pending submission 已被后续 projection/snapshot 承接”
- `command_id` 的保留窗口与覆盖规则
- `status.accepted_but_queued` 与 `accepted` 在 continuity 上如何等价

这里如果继续模糊，结果只会是：

- submit response 里有 `command_id`
- 后续 snapshot 里没有稳定 join key
- Android 被迫继续用 `request_id + session_id` 做长期弱绑定

完成定义：

- `docs/current/android_session_snapshot_facade_contract.md` 后续有明确可落地的升级目标
- Android detail store 的 `pending submission` 清理规则可以直接编码

### 6. facade 错误面与 HTTP status 表

当前 requirements 只列了最小错误码集合，但还没有把 facade-facing HTTP status 和 retry 读法冻住。

编码前至少要补齐：

- success path HTTP status / replay 响应矩阵：
  - 首次 `accepted` 返回什么 HTTP status
  - 首次 `accepted_but_queued` 返回什么 HTTP status
  - 幂等重放命中同一 `request_id`、同一 payload 时返回什么 HTTP status
  - 幂等重放时是否必须复用同一 `command_id + submit_ack + target_session_identity`
  - Android 是否需要区分 fresh accept 与 replayed accept；如果不区分，对外字段应如何保持稳定
- `401 unauthorized`
- `400 invalid_payload`
- `404 session_not_found`
- `409 session_identity_mismatch`
- `409 session_binding_unresolved` 或其他最终选定口径
- `409 workspace_unavailable` / `pc_offline` 是否保持 `409`
- `422 unsupported_action`、`unsupported_backend`、`unsupported_profile`、`unsupported_permission` 是否进入同一类业务拒绝
- 哪些错误允许 Android 重试
- 哪些错误只能 hard stop

固定规则：

- facade 对外不再暴露 `/control hello_ack did not advertise ...`
- facade 对外不再暴露 `direct_temporarily_unavailable`

完成定义：

- repo-side contract test 能直接断言 success / replay / reject 的 HTTP status + `error_code`
- Android 不需要继续把 transport-era error mapping 留在主线代码里

## 建议输出物

在正式写 handler 之前，至少应先产出下面 4 个 planning / contract 输出物：

1. 一份 facade appendix：
   - 专门冻结请求、回包、错误、action payload
2. 一份 action mapping 表：
   - `facade action -> command_type -> payload mapping -> ack semantics`
3. 一份 continuity appendix：
   - 说明 `command_id` 如何进入 `session-snapshot / projection`
4. 一份 repo-side contract test matrix：
   - 覆盖 request validation、idempotency、routing、error surface、success identity completeness、success/replay HTTP status matrix

如果这 4 项还没齐，就不应该直接开始大规模写 action handler 和 Android 页面层整合。

## 推荐开工顺序

当上面的 6 项都冻结后，repo-side 更合理的编码顺序是：

1. 先写 facade appendix、action mapping 表、continuity appendix
2. 再补纯 contract-level tests
3. 再落 `POST /v1/android/session-action` admission、routing、validator 骨架
4. 再按 action family 接 canonical `command_dispatch`
5. 最后再升级 `session-snapshot / projection` continuity，并与 Android 做 hard cut smoke

## 明确不该做的事

本轮不应：

- 直接沿用旧 `/control` `post-creation-session-action-contract-v1` 当 facade 正文
- 先只做 `reply/status`，再把其他动作留到“切换完成之后”
- 在 `session-snapshot` 还没有 `command_id` continuity 设计时就让 Android 改成 command-first pending model
- 让 Android 自己承担 `pc_id` 决策
- 提前把 `docs/current/*` 改写成“post-creation action 已全面切到 facade”

## 一句话结论

`POST /v1/android/session-action` 当前离“可编码”差的不是愿景，而是 6 个必须先冻结的合同缝：action appendix、`request_id` 规则、routing 与 locator 衔接、canonical command mapping、`command_id` continuity、以及 facade 错误面。把这 6 项先收成单一入口，后续实现和 Android 联调才不会重新掉回旧 `/control` 兼容语义。

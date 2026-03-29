# Android-Facing Session Action Facade Repo-Side 硬切统一执行方案（v0.1）

更新时间：2026-03-29

## 状态

本文覆盖同路径下 2026-03-28 之前的 session-action 方案文档。

从本文生效后，PC / VPS 侧对 Android-facing post-creation session action 的执行口径固定为：

- `session-action` 是正式 owner seam，不是 reply/status 的局部补洞接口
- `session-action` 首发就要承接所有用户可见的 post-creation session 操作
- 旧 mail `/control` lane 不再作为主线兼容通道设计，只保留回滚预案价值

本文不替代以下文档：

- 主线 owner note：`docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`
- 编码前置清单：`docs/plans/android_facing_session_action_facade_preflight_checklist_v0.1.md`
- 当前实现真相：`docs/current/README.md`
- 当前 Android 边界：`docs/current/android_runner_communication_contract.md`
- 当前 Android detail 读 seam：`docs/current/android_session_snapshot_facade_contract.md`
- Android 仓 companion plan：`E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-vps-first-multi-pc-session-action-facade-android-plan-v0.1.md`

本文回答的问题只有一个：

**repo-side 为了直接落地“长期架构最纯、协议最统一”的方案，应把 Android-facing `session-action` 冻结成什么样，并按什么门槛硬切。**

## 一句话结论

长期最优方案不是把所有能力塞进一个万能 endpoint，也不是长期保留多条并行通道，而是：

- `create-session`
- `session-action`
- `session-snapshot`

三条生命周期 seam 继续分职，但在 owner 模型上完全统一：

- 同一 admission：`android_app_token`
- 同一 submit 语义：`submit_ack`
- 同一 owner submit id：`command_id`
- 同一 target identity：`target_session_identity`
- 同一读回真相层：`session-snapshot / VPS-native projection`

其中所有用户可见的 post-creation session 操作，一次性统一进入 `session-action`。

## 最新进度（2026-03-29）

repo-side 当前已经补到两个明确切片：

- repo-internal `pc_control` current-session `reply/status/pause/resume/kill/end/answers/attachment_continuation` command path 已有代码实现
- 当前 `command_dispatch(reply|status|pause|resume|kill|end|answers|attachment_continuation)` 会在 PC 本地复用现有 post-creation mail path
- 当前 result 侧会回投 `result.structured_payload.kind=session_action_result`
- Android-facing `POST /v1/android/session-action` 也已扩到同一 current-session session-action first slice
- 当前 facade submit success/replay 会返回 `command_id + submit_ack + target_session_identity`
- 当前 same-`request_id` + same canonical payload replay，会稳定复用同一 `command_id`
- 当前 first `accepted` / first `accepted_but_queued` / same-payload replay，repo-side 都固定返回 `HTTP 200`
- 当前 same-`request_id` + different canonical payload，repo-side 固定返回 `HTTP 409 request_id_conflict`
- 当前 `create-session` 已要求显式 `canonical_reply_recipient`，并会把它写入 durable session binding
- 当前 `session-action` recipient resolve 已先读 durable `canonical_reply_recipient`，legacy mail-born session 才 fallback 到历史 inbound mail sender；若两者都缺失，当前 rejected code 固定为 `session_recipient_unresolved`
- current-session route authority 已切到 `session_id` primary target，`target.workspace_id / target.thread_id` 都降为 supporting identity
- `session-snapshot` 当前也已开始暴露 `latest_session_action.command_id` continuity，用于 `reply/status/pause/resume/kill/end/answers/attachment_continuation` tranche 的 read-after-write join

这次进度应读成：

- canonical command-family seam 已从 internal slice 推进到 Android-facing current-session session-action facade tranche
- repo-side 已经有一条可运行的 app-facing `session-action` first slice，并且 current-session action family 已补齐到 `attachment_continuation`
- repo-side 也已把 `session_id only` routing 升格为 current truth，server-side 会在 submit/read 窗口内回收 canonical resolve

不应读成：

- 本文第 5-11 节定义的 route / continuity / contract gate 也已经全部完成
- projection / snapshot 的全 action-family `command_id` continuity 已闭环
- 当前 Android mail-first 边界已经改变

## 1. 固定方向

从本文生效后，repo-side 对 Android-facing session 生命周期的长期读法固定为：

1. `create-session` 负责创建 canonical session
2. `session-action` 负责对既有 session 发起 canonical action
3. `session-snapshot` 与 projection 负责返回 action 之后的 canonical session state

固定规则：

1. 不把创建、操作、读回混成一个万能接口。
2. 也不允许操作层长期分裂成 facade、mail、`/control` 三套 owner 模型。
3. `session-action` 必须从一开始就按“通用 post-creation action 家族”设计，而不是 reply/status 专属例外。

## 2. v0.1 首发 scope 冻结

`session-action` 的首发 scope 固定为所有用户可见的 post-creation session 操作：

- `reply`
- `status`
- `answers`
- `pause`
- `resume`
- `kill`
- `end`
- `attachment_continuation`

固定规则：

1. 不再以“先 reply/status，其他动作继续走 mail”作为主线完成态。
2. 内部可以分切片开发，但正式硬切门槛是以上动作都进入同一家 `session-action` owner seam。
3. 若其中任一用户可见动作仍需依赖旧 mail `/control` 才能正常使用，则不应宣布主线切换完成。

## 3. Android-Facing Facade Family

repo-side Android-facing facade family 固定为：

- `POST /v1/android/create-session`
- `POST /v1/android/session-action`
- `session-snapshot` 读入口

固定规则：

1. 三者同属 Android-facing app API family。
2. 三者在 owner 语义上保持一致，不再让 Android 感知 transport-level 内部机制。
3. Android 主路径不需要 transport token，也不需要参与 `/control hello / hello_ack`。

## 4. `session-action` 合同冻结

### 4.1 鉴权

`session-action` 固定使用：

- `Authorization: Bearer <android_app_token>`

固定规则：

1. 不要求 Android 持有 transport token。
2. live `vps_only` checkpoint 是否关闭旧 direct ingress，不应影响 Android 主线写路径。

### 4.2 请求形状

最小请求外壳固定为：

```json
{
  "request_id": "req_20260328_001",
  "action": "reply",
  "target": {
    "session_id": "thread_20260328_221449_05d3ae",
    "workspace_id": "workspace_cb2404bf828c",
    "thread_id": "thread_20260328_221449_05d3ae"
  },
  "reply": {
    "reply_text": "Please continue."
  }
}
```

字段边界固定为：

1. `request_id` 必需，是 Android-visible submit attempt 的稳定幂等键。
2. `action` 必需，且必须属于本文冻结的 action family。
3. `target.session_id` 必需，是 primary target identity。
4. `target.workspace_id` 推荐传，用于一致性校验，不是 primary route authority。
5. `target.thread_id` 可选，仅作 supporting identity / diagnostics。
6. action-specific body 按 `action` 展开，不再依赖 mail subject/body 语义。

### 4.3 Action family 要求

repo-side 第一轮就必须为以下动作给出 canonical request mapping：

- `reply`
- `status`
- `answers`
- `pause`
- `resume`
- `kill`
- `end`
- `attachment_continuation`

固定规则：

1. 每个动作都必须能映射到 canonical `command_dispatch`。
2. 不允许留下“先继续走 mail，后面再迁”的 owner 空洞。
3. attachment continuation 也应进入同一 owner 家族，不再靠 mail path 维持主线语义。

## 5. Server-Side Routing 与 authoritative identity

repo-side 必须把 canonical route resolve 责任收回服务端。

固定规则：

1. Android 以 `session_id` 为主提交目标。
2. repo-side 在 submit 窗口内解析出 canonical `pc_id + workspace_id + session_id`。
3. 若 Android 同时传了 `workspace_id` 或 `thread_id`，repo-side 只做一致性校验。
4. Android 不应被要求在 post-creation action 主路径上自己决定目标 `pc_id`。

命中结果至少要稳定区分：

- `session_not_found`
- `session_binding_unresolved`
- `session_identity_mismatch`
- `pc_offline`
- `workspace_unavailable`

## 6. Submit Ack、identity 与 continuity

### 6.1 成功回包

成功或排队成功时，返回形状固定为：

```json
{
  "command_id": "cmd:android-session-action:20260328_223000:ab12cd34",
  "submit_ack": {
    "ack_status": "accepted",
    "queue_position": 0,
    "reason": null,
    "error_code": null
  },
  "target_session_identity": {
    "pc_id": "pc-home",
    "workspace_id": "workspace_cb2404bf828c",
    "session_id": "thread_20260328_221449_05d3ae",
    "thread_id": "thread_20260328_221449_05d3ae"
  }
}
```

固定规则：

1. 成功回包必须返回：
   - `command_id`
   - `submit_ack`
   - `target_session_identity`
2. 成功回包的最小 `target_session_identity` 必须包含：
   - `pc_id`
   - `workspace_id`
   - `session_id`
3. `thread_id` 在 `v0.1` 只作 optional supporting identity / diagnostics，不是 Android hard requirement。
4. repo-side 不应暴露“success + partial identity，剩余字段再靠 projection / snapshot 补齐”作为标准成功路径。
5. 如果 submit 窗口内拿不出完整最小 identity，应 `rejected`，而不是 success + partial identity。

### 6.2 `status` 的 ack 语义

`status` 的 `v0.1` must-have 结论固定为：

1. Android 不要求 `status` 必须出现 `accepted_but_queued`。
2. repo-side 若把 `status` 冻结成只有 `accepted | rejected`，Android 可接受。
3. 若 repo-side 为复用通用 submit 模型保留 `accepted_but_queued`，Android 也可接受。
4. 但 `status.accepted_but_queued` 的状态机语义必须与 `accepted` 等价。
5. 它最多只允许影响提示文案，不应改变 continuity 或 owner 行为分支。

### 6.3 `command_id` 主锚点

同一次 action 的长期 continuity 锚点固定为：

1. `command_id` 是 authoritative 主锚点。
2. `request_id` 必须保留，但只作：
   - client-side idempotency key
   - retry dedupe key
   - supporting continuity key
3. projection / snapshot 必须能让 Android 以 `command_id` 建立 same-run continuity，而不是被迫长期依赖 `request_id + session_id`。

## 7. Canonical Command 映射

repo-side 必须把所有 `session-action` 动作统一映射到现有 canonical `command_dispatch` 主线。

固定规则：

1. 每个 action 都必须有稳定 `command_type` 与 payload 映射。
2. 不另起第二套 Android-only follow-up runtime。
3. `command_id`、`pc_id`、`workspace_id`、`session_id` 继续复用 canonical owner 语义。
4. `reply / answers / pause / resume / kill / end / attachment_continuation / status` 统一按 command family 读，不再由 mail bridge 承担 owner 语义。

## 8. Read-After-Write 真相层

repo-side 必须把所有 post-creation action 的 owner 真相层固定回：

- `session-snapshot`
- `VPS-native projection`

固定规则：

1. `submit_ack` 只回答“请求是否被接收”。
2. action 后续用户可见结果，必须能通过 projection / snapshot 继续读回。
3. Android 不应再把 canonical mail outcome 读成 post-creation action 的 owner truth。
4. projection / snapshot 侧不应把 `command_id` 完全抹掉，否则 Android 无法做强 continuity。

## 9. 稳定错误面

repo-side `v0.1` 至少应稳定支持：

- `unauthorized`
- `invalid_payload`
- `unsupported_action`
- `session_not_found`
- `session_binding_unresolved`
- `session_identity_mismatch`
- `pc_offline`
- `workspace_unavailable`
- `unsupported_backend`
- `unsupported_profile`
- `unsupported_permission`
- `backend_transport_unavailable`

固定规则：

1. `/control hello_ack did not advertise ...` 之类 transport-level 错误不应再进入新 seam 的公开错误面。
2. `direct_temporarily_unavailable` 之类旧 guarded lane 语义不应继续作为 Android-facing 主错误。

## 10. 硬切执行规则

内部实现可以分切片，但主线 acceptance 固定按硬切读法判断。

### 10.1 允许的内部切片

允许 repo-side 在实现过程中按以下顺序推进：

1. 冻结 facade 合同与 action family
2. 完成 server-side routing 与 canonical command 映射
3. 补齐 projection / snapshot continuity
4. 联合 Android 做端到端 hard cut 验证

### 10.2 不允许的主线完成态

下面这些状态都不应被读成主线完成：

1. 只有 `reply/status` 进入 `session-action`，其他动作还在 mail。
2. Android-facing 文档仍把旧 `/control` lane 写成兼容主路径。
3. projection / snapshot 还无法承接全部动作的读回。
4. UI 仍需按动作类型分别决定走 facade 还是 mail。

### 10.3 旧通道的定位

旧 mail `/control` lane 只保留：

- 回滚预案
- 内部诊断
- 历史证据参考

固定规则：

1. 它不再写进主线 owner 文档的正向行为定义。
2. 它不再作为 Android-facing 协议 acceptance 的组成部分。
3. 它不再成为“先上线再慢慢替换”的长期设计借口。

## 11. 验收门槛

repo-side 做完这一轮硬切后，至少应拿出下面 4 类证据。

### 11.1 contract-level 单测

- 所有 action family 的请求校验
- `session_id` primary target 路由校验
- `request_id` 幂等与冲突校验
- `reply` 的 `accepted | accepted_but_queued | rejected` 返回语义校验
- `status` 的 `accepted | rejected` 返回语义校验；若保留 `accepted_but_queued`，应验证其与 `accepted` 等价
- success path 必带完整最小 `target_session_identity`

### 11.2 runtime / store 单测

- 所有 action family 到 canonical `command_dispatch` 的映射
- `session_binding_unresolved`
- `session_identity_mismatch`
- `pc_offline`
- `workspace_unavailable`
- projection / snapshot 对 `command_id` continuity 的承接

### 11.3 roundtrip 证据

至少要有覆盖全部用户可见动作家族的 focused live 样本，证明：

1. Android `POST /v1/android/session-action`
2. repo-side admission
3. canonical `command_dispatch`
4. `command_ack`
5. Android-facing `submit_ack`
6. projection / snapshot 后续更新
7. Android 侧按同一 `command_id` 建立 continuity

### 11.4 不回归现有主线

repo-side 还必须证明：

- `create-session` 不回归
- `session-snapshot` 不回归
- 当前 `docs/current/*` 在真正硬切完成前不被误写成“已经全部切换完成”

## 一句话结论

**PC / VPS 侧长期最优执行方案，是让 `session-action` 一次性承接所有用户可见的 post-creation session 操作，并与 `create-session / session-snapshot` 组成统一的 Android-facing facade family；旧 mail `/control` 只保留回滚预案价值，不再参与主线 owner 设计。**

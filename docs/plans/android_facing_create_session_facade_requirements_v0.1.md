# Android-Facing CreateSession 薄 Facade Repo-Side 要求（v0.1）

更新时间：2026-03-26

## 状态

本文是 `mail_based_task_manager` 仓在 `VPS-first 多 PC` 主线下，对 Android-facing `CreateSessionCommand` first-pass 的 repo-side 同步要求说明。

它的作用是把 Android 仓刚冻结的：

- `薄 facade`
- `CreateSessionCommand`
- `submit ack + session binding`

压成 PC/VPS 侧可直接执行的 repo-side owner 要求。

它不替代以下文档：

- 主线 authority：`docs/plans/android_pc_vps_evolution_authority.md`
- 当前 owner note：`docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`
- 当前实现真相：`docs/current/README.md`
- 当前 Android 边界：`docs/current/android_runner_communication_contract.md`
- Android-facing contract：`E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-vps-first-multi-pc-android-facing-facade-authority-v0.1.md`
- Android-facing create-session contract：`E:\projects\android_task_manager\docs\taskmail\planning\android\taskmail-vps-first-multi-pc-create-session-command-contract-v0.1.md`

这些文档分别回答“主线是什么”“今天实现了什么”“Android 侧希望看到什么”。
本文回答的是：

**repo-side 下一刀为了接 Android-facing `CreateSessionCommand`，必须同步补哪些东西。**

## 一句话结论

repo-side 下一刀不该去：

- 扩旧 `/control` compatibility shell
- 把 `/pc-control` 直接宣布成 Android app protocol
- 再造一套与 `pc-control` 平行的新控制协议

repo-side 应做的是：

- 在现有 `pc_control_runtime` 之上补一层很薄的 Android-facing ingress
- 接 `CreateSessionCommand`
- 返回 `command_id + submit_ack + session_binding`

也就是说，改动方向应是“补业务边界”，不是“重写控制面”。

## 1. repo-side 必做项

### 1.1 新增 Android-facing ingress

repo-side 必须提供一条正式 Android-facing ingress。

固定要求：

- 它可以复用现有 host / port / runtime wiring
- 它不要求复用当前 operator-only debug path
- 它不要求 Android 直接扮演 `/pc-control` client
- 它不应把旧 `/control` compatibility 继续扩成多 PC 主线 app API

本文不冻结最终 endpoint 名称，但冻结角色边界：

- Android 连接的是 app-facing facade
- facade 内部再去驱动 `pc_control_runtime`

### 1.2 接受 `CreateSessionCommand`

repo-side 第一版必须接受下面这组 Android-facing 业务输入：

- `pc_id`
- `workspace_id`
- `prompt`
- `execution_policy`

允许可选输入：

- `mode`
- `timeout_seconds`
- `acceptance[]`
- `repo_path`
- `workdir`
- `source`

固定读法：

- `pc_id + workspace_id` 是主路由键
- `prompt` 是 Android-facing 语义；repo-side 应映射成内部 `task_text`
- `repo_path / workdir` 只作审计镜像或兼容镜像，不替代主路由键
- `execution_policy` 继续沿用现有控制面字段，不另起一套新字段

### 1.3 映射到内部 `command_dispatch(new_task)`

repo-side 必须把 Android-facing `CreateSessionCommand` 薄映射到现有内部主线：

- `command_type = new_task`
- `workspace_id` 继续走内部主路由
- fresh session 的 `command_dispatch.session_id = null`
- `prompt -> payload.task_text`
- `mode / timeout_seconds / acceptance / repo_path / workdir / source` 继续沿用内部 payload 语义

repo-side 当前不需要为了这条入口再发明第二套 runtime。

### 1.4 返回 `submit ack + session binding`

repo-side 第一版必须对 Android-facing 提交返回下面这组语义：

- `command_id`
- `submit_ack`
- `session_binding`

`submit_ack` 最少应包含：

- `ack_status`
- `queue_position`
- `reason`
- `error_code`

`session_binding` 最少应包含：

- `session_id`
- `pc_id`
- `workspace_id`

固定约束：

1. 当 `ack_status = rejected` 时，不返回 `session_binding`
2. 当 `ack_status = accepted | accepted_but_queued` 时，必须给出 `session_binding`
3. `session_binding` 可以与 `submit_ack` 组合返回，也可以在同一提交窗口内分段返回
4. repo-side 不得把“只有 `submit_ack`、没有 `session_binding`”暴露给 Android 页面层当作完整成功

### 1.5 补齐 `session binding` 责任

repo-side 必须承认：当前内部 `command_ack` 不天然等于 Android-facing 完整提交回包。

因此第一版必须明确一处 binding resolver 责任：

- 若 runtime 在接单时已能确定 `session_id`，则直接投影
- 若内部 `command_ack` 尚不带 `session_id`，则由 facade 层或其依赖的 binding resolver/store 补齐

这个补齐责任不能回推给 Android。

### 1.6 稳定拒绝码

repo-side 第一版必须稳定支持以下拒绝码：

- `unsupported_backend`
- `unsupported_profile`
- `unsupported_permission`
- `profile_model_unresolved`
- `workspace_unavailable`
- `pc_offline`

固定读法：

- 前 4 个优先对应 `execution_policy` admission / resolution
- `workspace_unavailable` 对应路由目标不可用
- `pc_offline` 对应目标节点不可投递

第一版暂不要求把 auth/quota/backend_transport 一次细分到底。

## 2. repo-side 明确不该做的事

这条切片里，repo-side 不应顺手把下面这些一起做掉：

- 不把 `/debug/pc-control/dispatch` 提升成 user-facing API
- 不把 `/pc-control` 直接宣布成 Android app protocol
- 不把旧 `/control` compatibility shell 继续扩成主线
- 不重写 `pc_control_runtime`
- 不在这一刀里同时做完整 `session snapshot / timeline / replay` API
- 不在这一刀里同时做 `reply / status / pause / resume / kill` 的 Android-facing facade
- 不为了“看起来零改动”而把 `session binding` 缺口留给 Android 客户端兜底

## 3. repo-side 推荐实现边界

第一版 repo-side 实现建议压成 4 小块：

1. Android-facing ingress/admission
2. `CreateSessionCommand -> command_dispatch(new_task)` mapper
3. `submit_ack + session_binding` assembler
4. 最小 binding resolver / store continuity

如果某块已经在现有 runtime/store 里有现成 seam，优先复用，不建议平行再造。

## 4. 验收门槛

repo-side 把这条切片做完后，至少应能拿出下面 4 类证据：

### 4.1 contract-level 单测

- `CreateSessionCommand` 请求校验
- `prompt -> task_text` 映射
- `pc_id + workspace_id` 路由校验
- `submit_ack + session_binding` 组合或分段语义校验
- `rejected` 时不返回 `session_binding`

### 4.2 runtime/store 单测

- `accepted`
- `accepted_but_queued`
- `rejected`
- `workspace_unavailable`
- `pc_offline`
- binding resolver 能把 `command_id` 补到稳定 `session_id`

### 4.3 roundtrip/fixture 证据

至少一条样本能走通：

1. Android-facing `CreateSessionCommand`
2. repo-side admission
3. 内部 `command_dispatch(new_task)`
4. `command_ack`
5. Android-facing `submit_ack + session_binding`

注意这条证据的目标不是重复证明 live `/pc-control` 基础链路，而是证明 Android-facing facade 这层边界本身成立。

### 4.4 不回归现有主线

repo-side 还必须证明：

- 当前 operator-only `/debug/pc-control/dispatch` 仍保持 debug 入口角色
- 当前 live `/pc-control` 行为不因 facade 入口引入回归
- 当前 `docs/current/*` 所描述的 current Android direct boundary 不被误写成“已经切到 Android-facing 新 API”

## 5. 下一刀之后再做什么

这条切片完成后，repo-side 的下一刀才适合继续收：

1. `session snapshot`
2. `timeline stream`
3. `replay`
4. `artifact download_ref`

也就是先把“能稳定提交并绑定 session”站稳，再扩完整读侧，而不是把写入口和读侧大改混在同一轮里。

## 一句话结论

**PC/VPS 侧当前最应该同步补的，不是第二套控制协议，而是一条能把 Android-facing `CreateSessionCommand` 薄投影到现有 `pc_control_runtime` 的正式 ingress，并稳定返回 `command_id + submit_ack + session_binding`。**

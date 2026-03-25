# VPS-only 开发支持测试目标

## 状态

- 日期：`2026-03-25`
- 作用域：仓库侧 `vps-only` 开发线的近期支持测试目标
- 文档层级：`docs/reference/`
- 相关文档：
  - `docs/plans/android_pc_vps_evolution_authority.md`
  - `docs/plans/vps_first_multi_pc_control_plane_mainline_v0.1.md`
  - `docs/plans/vps_first_multi_pc_phase1_execution_plan_v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-pc-vps-control-protocol-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-execution-policy-appendix-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-legacy-mail-to-control-plane-mapping-v0.1.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\platform\taskmail-result-artifact-errorcode-appendix-v0.1.md`

## 目的

本文冻结的是：

**在仓库侧推进 `vps-only` 开发线时，哪些测试必须先补齐，才能支撑 Phase 1 的协议、状态机和执行接线开发。**

这里的“支持测试”不是最终产品验收，也不是 Android UI 联调，而是：

1. 让 canonical 字段与状态机先有稳定回归保护
2. 让 `mail -> control-plane` 的兼容映射有明确测试约束
3. 让 `Codex / OpenCode` 的 `cli / sdk` 执行路径能被纳入新控制面而不是停留在旧 mail 语义里

## 总原则

`vps-only` 线下，测试目标必须以 canonical 控制面对象为中心，而不是以 mail 外壳为中心。

对真实 backend smoke，再额外固定两条执行约束：

1. 不挂进 `tests/` 主测试集，避免 full suite 每次都触发真实 smoke
2. 每次 smoke 结束都必须留下清理证据，证明临时进程或监听端口已经关闭

因此优先级应是：

1. `execution_policy`
2. `command_dispatch / command_ack`
3. `event`
4. `result`
5. `output_chunk`
6. `artifact_manifest`
7. legacy mail 语义到 canonical 对象的映射

mail 线相关测试仍然保留两种价值：

1. 兼容入口映射测试
2. 当前行为不被意外破坏的回归保护

但它不再是这条开发线的测试组织中心。

## 当前测试目标陈述

当前这一轮测试目标应收敛为：

**先补齐能支撑 `VPS-only` Phase 1 开发的 contract / mapping / state / execution / stream / artifact 六类支持测试，再在这个基础上推进实现。**

## P0 必补：协议与字段支持测试

### 1. `execution_policy` 字段测试

必须先覆盖：

1. `backend`
2. `profile`
3. `permission`
4. `backend_transport`
5. `resolved_model`

测试目标：

- 请求值与默认值的归一化稳定
- 非法值能被显式拒绝
- `effective_execution` / `effective_execution_policy` 回填字段稳定
- 不允许静默回退到别的 backend / profile / permission / transport

### 2. `result / artifact / error_code` 字段测试

必须先覆盖：

1. `result.final_status`
2. `result.summary`
3. `result.structured_payload.kind`
4. `result.effective_execution`
5. `artifact_manifest.artifacts[]`
6. `error_code / error_message / error_type`

测试目标：

- `accepted / running / awaiting_user_input / paused / done / failed / killed` 这些状态读法稳定
- `structured_payload.kind` 不被省略或混写
- `artifact_id` 与 `download_ref` 读法稳定
- `error_code` 保持 machine-readable，不与 `final_status` 混用

### 3. legacy mail -> canonical 字段映射测试

虽然当前主线是 `vps-only`，但开发早期仍需要这组支持测试，防止迁移时把已验证业务语义丢掉。

必须先覆盖：

1. `[OC] / [CX]` -> `command_type = new_task` + `execution_policy.backend`
2. `Repo / Workdir / Task / Timeout / Mode / Profile / Permission / Acceptance`
3. 普通 reply / `Answers:` / `/status` / `/pause` / `/resume` / `/kill`
4. 状态标签到 `event / result` 的映射

测试目标：

- 迁移复用的是“业务语义”，不是 mail 外壳字段本身
- mail 外壳字段只在 ingress/compatibility 层保留，不进入 canonical 主键

## P1 必补：控制面状态机与幂等支持测试

### 1. `command_dispatch -> command_ack`

必须先覆盖：

1. `command_id` 幂等
2. `accepted / accepted_but_queued / rejected`
3. `unsupported_backend`
4. `unsupported_profile`
5. `unsupported_permission`
6. `unsupported_backend_transport`
7. `profile_model_unresolved`

测试目标：

- 同一 `command_id` 重投不产生重复执行
- 不支持的策略显式拒绝，而不是静默降级

### 2. `event` 与 `result` 收口

必须先覆盖：

1. `event_id` 去重
2. `result_id` 去重
3. 同一 `command_id` 只有一个 canonical result
4. `running` 事件可带回 `effective_execution`
5. `awaiting_user_input / paused / done / failed / killed` 的稳定状态收口

测试目标：

- 状态机先稳定，再让 UI 或 exporter 消费
- 不依赖文本流反推最终状态

### 3. `pc_id / workspace_id / session_id / run_id` 基本归属

作为开发支持测试，至少要保证：

1. `workspace` 从属于单一 `pc_id`
2. `session` 从属于单一 `workspace_id`
3. `run` 从属于单一 `session_id`
4. follow-up 仍回原始 `pc_id + workspace_id + session_id`

这组测试不要求完整多 PC 联网环境，但要求本地模型和持久化层已经守住归属关系。

## P2 必补：执行接线支持测试

### 1. `backend_transport` 路由测试

必须覆盖：

1. `backend_transport = cli`
2. `backend_transport = sdk`
3. backend 默认 transport
4. 不支持 transport 的拒绝路径

测试目标：

- 新控制面字段能真正决定执行路径
- 不允许声明 `sdk` 却静默走回 `cli`

### 2. `Codex SDK` / `OpenCode SDK` 最小真实任务测试

对两个 backend 都需要最小真实任务验证。

最小任务优先使用低风险动作，例如：

- 创建一个文本文件
- 写入固定文本
- 返回固定格式回复

测试目标：

- `sdk` 路径真实可用
- `backend / profile / permission / backend_transport` 的映射可观察
- `resolved_model` 或等价实际生效信息可回填

### 3. follow-up 与继承规则测试

必须覆盖：

1. `new_task` 显式给 `backend`
2. follow-up 默认继承当前 `session` 的 `backend / profile / permission / transport`
3. 允许覆盖时，覆盖逻辑可被明确验证

## P3 必补：流式与 artifact 支持测试

### 1. `output_chunk`

必须先覆盖：

1. `stream_id + seq` 排序
2. 去重
3. 缺洞检测
4. 基础 replay request 所需游标语义

测试目标：

- 未来 UI 可消费流式输出
- 流式输出不替代结构化 `event / result`

### 2. `artifact_manifest`

必须先覆盖：

1. `artifact_id`
2. `kind`
3. `name`
4. `content_type`
5. `size`
6. `download_ref`

测试目标：

- 最小 artifact metadata 可被稳定投影
- `artifact_id` 不与底层 `file_id` 或本地路径混写

### 3. waiting-state / question-set

因为 Phase 1 已把 `awaiting_user_input` 纳入正式状态集合，所以还需要覆盖：

1. `question_set`
2. 单题自由文本回答
3. 多题 `Answers:` 映射
4. paused 后 resume 带答案与不带答案

## 当前明确不作为主目标

以下事项不是当前这轮“开发支持测试”的主目标：

1. Android UI 切换完成
2. 多用户 ACL
3. 跨 `PC` 热迁移
4. 共享 workspace 的多 `PC` 执行
5. mail 全面退场

但与上面不同，以下内容虽然不是“完整产品联调”，仍属于当前必须尽早补的支持测试：

1. `pc_hello / hello_ack`
2. `connection_epoch` fencing
3. `workspace_snapshot`

原因是它们已经是 Phase 1 最小骨架的一部分，不能再像上一版那样完全排除在目标外。

## 证据要求

每完成一项支持测试，至少留下三类材料：

1. 一个可复跑入口
   - 单测、集成测试、fixture harness 或脚本
   - 对真实 backend smoke，默认优先独立脚本，不进入 `tests/` 主测试集
2. 一份验证结果
   - 成功 / 失败、日期、环境、关键结论
3. 一份证据路径
   - JSON fixture、日志、`result.json`、测试输出或其他可复核产物
   - 对真实 backend smoke，证据中还必须包含清理结果，例如端口关闭、sidecar 记录删除、临时服务退出等

如果只有口头结论，没有可复跑入口和证据路径，这项测试不算关闭。

## 当前通过条件

当前这一轮可以认为“支持测试补齐到足以继续开发”，至少需要满足：

1. canonical 字段测试已经覆盖 `execution_policy / result / artifact / error_code`
2. mail 兼容映射测试已经覆盖首批 `new_task / reply / status / pause / resume / kill`
3. `command_dispatch / command_ack / event / result` 的状态机与幂等测试已经存在
4. `Codex` 与 `OpenCode` 的 `cli / sdk` 执行路径至少各有一条可复跑最小真实任务路径
5. `output_chunk` 与 `artifact_manifest` 至少有最小 contract 测试
6. 已知缺口被明确记录，而不是被默认忽略

## 更新条件

出现以下任一情况时，应更新本文：

1. Phase 1 协议字段冻结发生变化
2. `result / artifact / error_code` 附录发生变化
3. legacy mail 到 canonical 的映射范围变化
4. `Codex SDK` 或 `OpenCode SDK` 的执行接线能力发生变化
5. 仓库开始推进 Phase 2 或更高阶段，导致当前支持测试目标需要扩展

# Thread Management State Model Plan (Legacy Draft)

> 文档层级：Layer 4（历史归档）
>
> 状态：Legacy
> 日期：2026-03-17
> 适用范围：`mail_based_task_manager` 当前仓库的线程管理、状态模型与邮件保留语义
> 关联文档：`docs/plans/coding_backlog.md`
> 说明：本文内容已并入 `docs/plans/coding_backlog.md`，保留仅用于回溯讨论上下文。

## 0. 目的

本文用于收口线程管理的内部状态模型。

目标不是立即改代码，而是先固定后续规划中的几个核心判断：

1. `DONE` 邮件应视为用户已收到结果的回执，不应再按瞬时进度邮件处理
2. 线程状态不能继续用单一枚举承载所有语义
3. 后续设计应把“执行状态”“生命周期”“健康状态”“邮件保留类型”拆开

## 1. 当前问题

当前仓库已经有 `run status -> thread status -> mail label` 的稳定链路，但这条链路过于线性。

当前表现出的主要问题有：

- `RUN_STATUS_SUCCESS -> THREAD_STATUS_DONE -> [DONE]` 是直通映射
- live mailbox 里的 task status mail 目前按 replacement 语义处理
- `[DONE]` 也被纳入了可清理状态
- 线程是否仍属于“当前活跃工作”与“上一轮执行是否成功”仍然混在一起
- “卡死 / 长时间无进展 / 宿主失联”这类生产问题没有独立状态轴

这会导致两个明显的用户层问题：

1. `[DONE]` 作为结果回执被错误当成了可替换的瞬时状态
2. 用户无法清楚区分：
   - 这条线程现在是否在跑
   - 这条线程是否仍算活跃工作
   - 这条线程是否卡死
   - 这封邮件是否应该保留

## 2. 核心结论

后续规划应固定以下结论。

### 2.1 `[DONE]` 必须保留

`[DONE]` 表示：

- 这一轮执行已经完成
- 用户已经收到结果
- 系统已经向用户给出明确回执

因此：

- `[DONE]` 不应再被当成可替换进度邮件
- 后续线程继续时，可以继续发新的状态邮件
- 但不应删除之前的 `[DONE]`

同理，`[FAILED]`、`[KILLED]` 也更接近“结果回执”而不是“纯进度提示”。

### 2.2 线程状态必须分轴

后续模型不应再把所有语义都压进一个 thread status。

至少应拆成 4 条轴：

1. 执行状态
2. 生命周期状态
3. 健康状态
4. 邮件保留类型

### 2.3 `[SYNC]` 不进入这套线程状态模型

`[SYNC]` 是全局只读控制邮件，不属于 task thread 的状态流转。

它应继续作为单独规则处理，不与 task thread 的状态机混合。

## 3. 目标状态模型

## 3.1 执行状态

执行状态描述“当前或上一轮执行怎样了”。

第一版建议固定为：

- `accepted`
- `queued`
- `running`
- `waiting_user`
- `paused`
- `done`
- `failed`
- `killed`

语义：

- `accepted`：邮件已接收，准备进入调度
- `queued`：等待 workspace 执行槽位
- `running`：正在执行
- `waiting_user`：等待用户输入
- `paused`：用户显式冻结
- `done`：上一轮成功完成
- `failed`：上一轮失败
- `killed`：上一轮被人工或系统中断

说明：

- `done`、`failed`、`killed` 仍然保留在执行状态轴内
- 它们表示的是最近一轮执行结果，不表示线程生命周期结束

## 3.2 生命周期状态

生命周期状态描述“这条线程还算不算当前活跃工作”。

第一版建议固定为：

- `active`
- `ended`

第二阶段可保留扩展位：

- `archived`

第一版语义：

- `active`：仍属于当前工作集
- `ended`：用户主动结束，不再默认出现在活跃视图里，但仍可恢复

为什么第一版不直接上 `archived`：

- 当前最迫切的是解决“用户主动结束”和“之后可以恢复”
- `archived` 更适合作为二阶段整理与长期归档能力

## 3.3 健康状态

健康状态描述“这条线程在运行/托管层面是否正常”。

建议固定为：

- `normal`
- `stale`
- `suspected_stuck`
- `orphaned`

语义：

- `normal`：状态正常
- `stale`：长时间未更新，但未必异常
- `suspected_stuck`：显示为运行中，但长时间没有明显进展
- `orphaned`：状态仍像在跑，但宿主或工作进程已经不存在

说明：

- 健康状态不是业务状态
- 它不应替代 `failed`、`paused`、`waiting_user`
- 它是用户与运维判断“要不要干预”的依据

## 3.4 邮件保留类型

这里不用“邮件投影状态”这个名字，后续统一叫：

`邮件保留类型`

原因：

- 这个词更直白
- 它强调的是 live mailbox 中的保留与替换语义
- 它不容易被误解成新的业务状态

建议固定为 3 类：

- `progress`
- `action_required`
- `receipt`

语义：

- `progress`：进度提示，可被后续进度更新替换
- `action_required`：要求用户动作，应至少保留到动作完成
- `receipt`：结果回执，应保留

## 4. 邮件标签到保留类型的映射

第一版建议映射如下：

| Mail Label | 保留类型 | 说明 |
| --- | --- | --- |
| `[ACCEPTED]` | `progress` | 初始接收提示，可替换 |
| `[RUNNING]` | `progress` | 过程提示，可替换 |
| `[STATUS]` | `progress` | 查询性状态，可替换 |
| `[QUESTION]` | `action_required` | 需要用户回答，应保留到处理完成 |
| `[PAUSED]` | `action_required` | 需要用户显式恢复，应保留 |
| `[DONE]` | `receipt` | 用户已收到结果，应保留 |
| `[FAILED]` | `receipt` | 用户已收到失败结果，应保留 |
| `[KILLED]` | `receipt` | 用户已收到中断结果，应保留 |

补充说明：

- `receipt` 并不意味着线程结束
- `progress` 是否严格只保留一封，可后续再细化
- 第一优先级是先把 `[DONE]` 从可删集合中剥离

## 5. 用户动作与状态轴的关系

后续协议应按以下原则映射用户动作。

### 5.1 `/end`

影响：

- 修改生命周期状态：`active -> ended`

不直接改变：

- 执行结果
- 历史邮件
- thread archive

### 5.2 `/resume`

可能影响：

- `ended -> active`
- `paused -> active` 后恢复执行路径

用户语义：

- 继续这条线程

系统实现：

- 可以 native resume
- 也可以 fresh continuation + 本地 recap

### 5.3 `/kill`

影响：

- 执行状态变为 `killed`

不直接等于：

- 生命周期结束

### 5.4 `/rerun`

影响：

- 在同一线程上启动新的执行尝试

不应自动改变：

- 生命周期状态

## 6. 生产视角下的最小可见状态集合

如果站在生产运维与用户协作的角度，最小可见集合建议是：

### 6.1 用户默认主视图

- `active`
- 执行状态
- 最近摘要
- 最后更新时间
- 是否 `waiting_user`
- 是否 `suspected_stuck`

### 6.2 深入详情视图

- thread id
- workspace id
- session id
- lifecycle
- execution status
- health
- current task id
- queued task id
- backend session resumable
- latest run status
- latest run finished at

## 7. 分阶段落地建议

### Phase 1：修正邮件保留语义

目标：

- `[DONE]` 不再属于可删状态
- `[FAILED]`、`[KILLED]` 一并评估是否进入回执保留集
- 文档先更新到 plan 层

### Phase 2：引入生命周期轴

目标：

- 新增 `active/ended`
- 增加 `/end`
- `/resume` 可恢复已结束线程

### Phase 3：引入全局线程管理入口

目标：

- `Subject: [SESSIONS]`
- 支持 `list active`、`list all`、`list stuck`
- 支持 `resume <thread_id>`、`end <thread_id>`、`kill <thread_id>`

### Phase 4：引入健康状态

目标：

- 定义 `normal/stale/suspected_stuck/orphaned`
- 暴露 heartbeat / 进度时间戳
- 允许用户识别“线程是否不工作了”

### Phase 5：收口 continuation 语义

目标：

- 用户只理解“继续线程”
- 系统内部决定 native resume 还是 fresh continuation
- 本地状态成为上下文真相层，而不是把真相压在 native session 上

## 8. 与当前实现的主要差距

当前实现与本计划相比，主要缺口是：

- `DONE` 仍然属于 live mailbox 可删状态
- thread status 仍是单轴
- lifecycle 尚未正式存在
- health 尚未正式存在
- `/sessions` 仍是 workspace 内只读列表，不是全局线程管理入口

## 9. 结论

后续规划应固定成下面这套方向：

- `[DONE]` 是回执，必须保留
- 线程状态不能继续单轴承载全部语义
- 至少拆成执行状态、生命周期状态、健康状态、邮件保留类型 4 层
- 用户管理线程时，应理解“继续 / 结束 / 恢复 / 查看 / 判断是否卡死”
- 系统内部不应再把这些问题都折叠进一个简单的 thread status 枚举里

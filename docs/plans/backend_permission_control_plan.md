# Backend Permission Control Plan

> 文档层级: Layer 2(当前仓库改造计划)
>
> 适用范围: `mail_based_task_manager` 当前仓库本体
>
> 本文定义的是未来要落地的后端权限控制字段方案，不代表当前代码已经支持该能力。

## 0. 当前背景

- 日期: 2026-03-16
- 当前仓库已经有一条稳定的 `profile` 传播链:
  - 初始邮件 / reply 解析
  - `ParsedMailAction`
  - `TaskSnapshot`
  - `ThreadState` / `SessionState`
  - `CodexAdapter` / `OpenCodeAdapter`
- 当前仓库也已经有全局能力开关:
  - `enable_web_search`
  - `profile -> model` 映射
- 当前缺口:
  - 没有一个显式、可持久化、可继承的 `permission` 字段
  - `Codex` / `OpenCode` 的执行权限仍然主要由当前 adapter 默认值或宿主 CLI 配置决定

这意味着:

1. 用户无法在单个线程 / session 上显式提权
2. 用户也无法显式把一个已经提权的线程恢复到默认权限
3. 状态邮件和快照里看不到当前线程到底在用什么权限级别

## 1. 文档目标

本文只回答当前仓库里的 4 个问题:

1. 要不要引入 `permission` 字段
2. 如果引入，字段语义是什么
3. 这个字段如何沿现有 mail -> snapshot -> thread -> adapter 链传播
4. `Codex` 和 `OpenCode` 分别如何映射到实际执行层

结论是:

**应该引入一个与 `profile` 同级的可选 `permission` 字段，并把“缺省继承、显式重置、后端投影”固定成正式方案。**

## 2. 设计目标

这条线的目标是:

1. 保持当前架构稳定，不单独发明一套新的权限子系统
2. 让用户能在邮件协议里显式请求更高权限
3. 让“这轮没写权限字段”时仍能延续当前 thread/session 的权限
4. 让用户可以显式恢复到默认权限，而不是只能一直继承
5. 把后端差异封装在 adapter 内部，而不是暴露到邮件协议里

## 3. 非目标

本文不做以下事情:

1. 不定义多租户权限模型
2. 不引入用户 / 组织级 ACL
3. 不改变 `enable_web_search` 的全局能力边界
4. 不试图从自然语言里猜“请给我最高权限”
5. 不永久修改用户机器上的全局 Codex / OpenCode 配置文件

## 4. 核心决策

### 4.1 字段名

字段名建议固定为:

```text
permission
```

邮件头部使用:

```text
Permission: highest
```

### 4.2 第一版允许值

第一版建议只支持两个显式值:

- `default`
- `highest`

同时保留“字段缺省”的第三种语义:

- 省略该字段

### 4.3 字段语义

三种情况的语义固定如下:

1. 省略 `permission`
   - 不改变当前线程 / session 的权限
   - 如果这是新任务且没有历史值，则使用后端当前默认权限

2. `permission = default`
   - 显式恢复到后端默认权限
   - 不再沿用前一轮已持久化的提权状态

3. `permission = highest`
   - 显式请求当前仓库支持的最高权限执行模式
   - 具体如何实现由对应 adapter 决定

### 4.4 为什么不用布尔值

不建议做成 `elevated: true|false` 或 `dangerous: true|false`。

原因有三点:

1. 布尔值不能自然表达“缺省继承”
2. 布尔值很难表达“显式恢复默认”与“没有覆盖”之间的区别
3. 将来如果要加 `restricted` / `workspace_write` 之类的中间档位，枚举值更稳定

## 5. 用户协议建议

第一版只支持结构化头部，不做自然语言识别。

### 5.1 初始任务邮件

示例:

```text
Subject: [OC] Refactor floor_shear

Repo: D:\proj\my_repo
Workdir: src
Profile: strong
Permission: highest

Task:
Refactor the module without changing the API.
```

### 5.2 reply / /resume / /new

示例:

```text
/resume
Permission: highest
Please continue with the cleanup.
```

或:

```text
/new
Permission: default
Mode: analysis_only
Task:
Only analyze the issue and list risks.
```

### 5.3 第一版不支持的写法

第一版不建议支持:

- “给你最高权限”
- “提一下权”
- “按管理员模式跑”

这些都不应在 parser/intent parser 里做自然语言猜测。

## 6. 传播链设计

建议完全复用现有 `profile` 的传播路径，只是把字段扩展成:

```text
mail -> ParsedMailAction -> TaskSnapshot -> ThreadState/SessionState -> adapter
```

### 6.1 入口层

- `parser.py`
  - 初始任务邮件新增 `Permission:` 头解析
- `intent_parser.py`
  - reply / `/resume` / `/new` 新增 `Permission:` 头解析

### 6.2 中间模型层

以下模型建议新增 `permission: str | None`:

- `ParsedMailAction`
- `TaskSnapshot`
- `ThreadState`
- `SessionState`

### 6.3 编译层

`task_compiler.py` 的继承规则建议直接仿照 `profile`:

- action 带 `permission` 时，使用 action 值
- action 未带 `permission` 时，继承 thread state 当前值
- 对于全新线程，如果没有历史值，则为 `None`

### 6.4 持久化层

建议:

- snapshot 落盘时保存 `permission`
- thread/session state 落盘时保存 `permission`
- 这样 `/resume`、follow-up、runner restart recovery 都能保持一致

### 6.5 呈现层

状态邮件 / reporter 建议显示当前 `Permission`

目的不是暴露内部实现，而是让用户能明确看到:

- 当前线程是不是已经处于提权状态
- 当前回复里没有写 `Permission:` 时到底会继承什么

## 7. 与现有配置的关系

### 7.1 与 `profile` 的关系

- `profile` 解决“用哪个模型”
- `permission` 解决“以什么执行权限运行”

两者必须保持独立，不能互相隐式推导。

### 7.2 与 `enable_web_search` 的关系

`permission` 不应隐式打开被全局关闭的能力。

也就是说:

- `enable_web_search = false`
  - 即使 `permission = highest`，也不应自动变成可联网搜索
- `enable_web_search = true`
  - `permission` 只决定执行权限，不重新定义搜索能力本身

这样可以保持:

- “能力开关”由配置决定
- “执行权限级别”由线程 / session 决定

## 8. Codex 映射方案

对 `CodexAdapter`，建议固定如下:

### 8.1 `default`

- 保持当前现有行为
- 继续沿用当前命令结构
- `--search` 仍然只由 `enable_web_search` 决定

### 8.2 `highest`

映射为:

```text
--dangerously-bypass-approvals-and-sandbox
```

这意味着:

- 跳过 approval
- 跳过 sandbox
- 这是 Codex 当前 CLI 已公开暴露的最高权限模式

### 8.3 resume 场景

`exec` 与 `exec resume` 都应使用同一套权限投影规则。

也就是说:

- 新 run 使用 `highest`
- native resume 也使用 `highest`

不能出现“新 run 提权了，但 resume 又偷偷退回默认”的不一致。

## 9. OpenCode 映射方案

`OpenCode` 这条线不适合做成简单的命令行参数，因为其权限更接近配置层。

### 9.1 `default`

- 保持当前现有行为
- 继续使用宿主上的正常 OpenCode 配置
- 不额外注入提权 overlay

### 9.2 `highest`

建议实现成:

**run-scoped merged config overlay**

也就是:

1. adapter 在当前 run 目录生成一个临时 OpenCode 配置文件
2. 该配置基于当前已解析的基础配置合并而来
3. 只覆盖仓库关心的 `permission` 相关字段
4. 通过 `OPENCODE_CONFIG` 只作用于当前 subprocess

### 9.3 为什么必须做 merged config

不建议直接写一个最小临时 JSON 然后把 `OPENCODE_CONFIG` 指过去。

原因是:

1. OpenCode 的 provider/model/options 也在配置文件里
2. 直接替换整份配置，可能把现有 provider、agent、model 配置冲掉
3. 当前仓库已经有“真实 CLI 正常跑”的基线，不能为了提权把基础配置破坏掉

因此更稳的约束是:

- 临时权限配置必须是 merged config
- 只能覆盖权限相关项
- 不能永久写回用户全局配置

### 9.4 “最高权限”的定义边界

对 OpenCode，`highest` 不应直接定义成“复制宿主机上所有未知权限规则”。

更稳的做法是:

- 由仓库在 adapter 内维护一份明确的“最高权限投影”策略
- 只覆盖当前 mail runner 真正依赖的权限集合
- 若未来 OpenCode 增加新的权限键，再由仓库显式更新映射

这样做的好处是:

1. 行为可测试
2. 行为可审计
3. 不依赖用户本机偶然存在的 agent/permission 细节

## 10. reporter 与审计建议

建议状态输出里增加:

```text
Permission: highest
```

推荐出现在:

- 状态邮件正文
- snapshot / thread / session 持久化结果

目的:

1. 用户能知道当前线程处于什么权限
2. 回放问题时可以从历史状态看出提权是否生效
3. 以后如果引入更多权限档位，也能保持同样的可见性

## 11. 第一版编码范围建议

第一版建议只做以下内容:

1. 增加 `permission` 字段
2. parser / intent parser 支持 `Permission:` 结构化头
3. task compiler 支持“缺省继承”
4. thread/session/snapshot 持久化
5. Codex/OpenCode adapter 的实际投影
6. reporter 显示当前权限

第一版先不要做:

1. 自然语言提权识别
2. 中间档位权限
3. 全局 UI/面板级权限切换
4. 非邮件入口的权限控制协议

## 12. 建议测试范围

至少应覆盖:

1. 新任务 `Permission: highest`
2. reply 不写 `Permission` 时继承旧值
3. reply 写 `Permission: default` 时显式恢复默认
4. `/resume` 沿用当前权限
5. `CodexAdapter` 在 `highest` 时带上危险参数
6. `CodexAdapter` 在 `default` / `None` 时不带危险参数
7. `OpenCodeAdapter` 在 `highest` 时生成 run-scoped merged config
8. `OpenCodeAdapter` 在 `default` / `None` 时保持当前路径不变
9. 状态邮件能显示当前 `Permission`

## 13. 与当前协议文档的关系

这份文档目前只属于 `docs/plans/`，还不应写入 `docs/current/`。

原因很简单:

- 当前代码还没有实现该字段
- 当前邮件协议也还没有正式支持 `Permission:`

因此在代码落地前，当前行为真相仍然以 `docs/current/*` 为准。

## 14. 一句话结论

对于当前仓库，最稳的做法不是“直接把两个 backend 改成永远最高权限”，而是:

**新增一个与 `profile` 同级的 `permission` 字段，支持 `default|highest`，缺省继承旧值，并把 Codex/OpenCode 的差异封装到 adapter 投影层。**

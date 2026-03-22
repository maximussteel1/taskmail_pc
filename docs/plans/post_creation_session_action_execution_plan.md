# Post-Creation Session-Action Execution Plan

## 状态

- 日期：2026-03-22
- 范围：仓库侧 direct post-creation session action 的第一份执行计划
- 层级：Layer 2 仓库实现计划
- 前置冻结：`docs/plans/post_creation_session_action_contract_v1.md`
- 当前状态：planning only；不改写 `docs/current/*`

## 目标

把 shared `post_creation_session_action_contract_v1` 收口成一份仓库侧可执行顺序，明确：

1. PC / VPS 侧先按什么顺序落地
2. 哪些代码面需要复用当前 canonical mail 行为
3. 哪些 rejection / closeout / parity 字段必须先补齐
4. 在什么证据到位之前，`direct reply` / direct `/status` 仍然不能提升为 current protocol

本计划当前只服务于第一批 v1 scope：

- `current-session plain reply`
- `current-session /status`

本计划当前明确不扩 scope 到：

- quick answer
- multi-question `Answers:`
- paused `/resume`
- attachment continuation
- targeted-session variant
- cross-workspace switching

## 当前起点

截至 2026-03-22，仓库侧已经具备这些可复用前提：

- `mail_runner.app` 已经有 current mail ingress 下的 canonical existing-session action 路径：
  - `_process_existing_thread_mail(...)`
  - `_send_current_status_query(...)`
- `mail_runner.thread_store` 已经有 current-session 解析基础：
  - `load_session_state(...)`
  - `resolve_thread(...)`
- `mail_runner.relay_server.direct_actions` 已经有 Phase 2 direct `new_task` 的 packet validation / handler / classifier 骨架
- `mail_runner.canonical_run_summary` 与 `mail_runner.taskmail_closeout` 已经能产出：
  - `request_id`
  - `ingress_message_id`
  - `packet_id`
  - `last_summary`
  - `terminal_mail_subject`
  - `same_run_bind`

但当前仍然缺这些东西：

- direct post-creation action 的独立 packet validator / handler
- `workspace_id + session_id` 直达 current session 的服务端解析与 supporting `thread_id` 校验
- `direct /status` 的 accepted path
- `direct reply` 的 first-slice plain-reply bridge
- 面向 post-creation action 的 closeout 工件

因此，这一轮不应该发明新的业务 truth layer，而应该优先把 direct action 桥接回当前 canonical mail ingress。

## 固定前提

本计划默认沿用 shared contract 已冻结的边界：

- ownership 不扩写 `phase2_direct_outbound_contract_v1.md`
- 仓库实现也不应把 `new_task` handler 和 post-creation handler 混成同一份模糊 contract
- target boundary 固定为 `current_session`
- canonical target identity 固定为 `workspace_id + session_id`
- `thread_id` 只作 supporting identity
- Android packet 只声明 canonical current session target 与 user intent
- server side 负责 bridge 到现有 canonical mail reply / status ingress
- accepted 后的 user-visible outcome 仍按当前 canonical mail truth layer 产出
- `switch_blocker` 是 parity / closeout 严重度，不是普通 ack success/error 替代物

## 实施顺序总览

仓库侧推荐按下面顺序落地：

1. 先抽出 current-session target resolver 与 direct bridge seam
2. 先做 direct `/status`
3. 再做 direct `reply`
4. 最后补齐 closeout / parity / live closeout 证据

排序原因：

- `/status` 不引入新 run，范围更窄，更适合作为第一条 post-creation accepted path
- `reply` 涉及 parser、session lifecycle、question / paused reject boundary，风险明显更高
- 如果 resolver、classification、closeout 没先稳定，先做 `reply` 很容易把问题混成“协议问题 + 运行时问题 + closeout 问题”

## 推荐模块边界

仓库实现建议保持模块职责清晰：

- `mail_runner/relay_server/direct_actions.py`
  - 继续承接 Phase 2 direct `new_task`
- 新增 `mail_runner/relay_server/post_creation_actions.py`
  - 承接 post-creation direct action 的 packet 解析、scope gate、classifier、handler
- 新增一个仓库内 bridge seam（文件名可微调）
  - 推荐类似 `mail_runner/session_action_bridge.py`
  - 负责把 accepted direct action 桥接进当前 canonical mail ingress

不推荐直接把 post-creation v1 继续塞进现有 `direct_actions.py` 的 `new_task` 分支里，否则后续很难保持：

- `phase2` 仍然只等于 `new_task`
- post-creation v1 的 rejection 分类独立可读
- `reply` / `/status` 的 closeout 证据独立演进

## WP1：冻结 current-session resolver 与 bridge seam

### 目标

让 direct post-creation action 能在 server side 只凭 `workspace_id + session_id` 找到 canonical current session，并把 supporting `thread_id` 当作校验字段而不是主定位字段。

### 代码面

- `mail_runner/thread_store.py`
- `mail_runner/app.py`
- `mail_runner/relay_server/post_creation_actions.py`（新）
- `mail_runner/session_action_bridge.py`（新，文件名可微调）

### 工作内容

1. 新增一个 current-session resolver：
   - 输入：`workspace_id`、`session_id`、可选 `thread_id`
   - 主解析：`load_session_state(workspace_id, session_id, ...)`
   - supporting check：若给了 `thread_id`，必须与 resolved session 的 canonical `thread_id` 一致
2. 固定 structured rejection code：
   - `session_identity_unresolved`
   - `session_identity_mismatch`
   - `current_session_only_violation`
3. 提供一个 direct-to-mail bridge helper：
   - synthesize 一个本地 ingress `MailEnvelope`
   - 写入 `X-TaskMail-Direct: 1`
   - 写入 `X-TaskMail-Relay-Request-Id`
   - 写入 `X-TaskMail-Relay-Packet-Id`
   - 通过 `capsule_state = {workspace_id, session_id, thread_id}` 进入 `_process_existing_thread_mail(...)`
4. 第一轮 bridge 不要求 Android 构造 mail `In-Reply-To` / `References`
5. 第一轮 bridge 也不允许 `repo_path + workdir` fallback target locating

### Done When

- 接受一个 current-session packet 时，server side 能只靠 `workspace_id + session_id` 找到目标 session
- `thread_id` 不一致会返回稳定 hard-stop code，而不是静默改投
- direct action 可以通过 synthesize ingress envelope 复用当前 `_process_existing_thread_mail(...)`

## WP2：先落 direct `/status`

### 目标

先把第一条 post-creation accepted path 做成最小闭环：

- packet accepted
- bridge 到 canonical current-session status ingress
- 仍产出 canonical `[STATUS]` mail

### 代码面

- `mail_runner/relay_server/post_creation_actions.py`（新）
- `mail_runner/session_action_bridge.py`（新）
- `mail_runner/app.py`
- `tests/test_relay_server_direct_actions.py`
- `tests/test_relay_server_runtime.py`
- `tests/test_app_phase3.py`

### 工作内容

1. 新增 `action = status` packet validator：
   - `schema_version = post-creation-session-action-contract-v1`
   - `dispatch_metadata.action = status`
   - `target.scope = current_session`
   - `status = {}`
2. direct `/status` accepted 后：
   - synthesize ingress mail body 为 canonical `/status`
   - 通过 bridge seam 进入 `_process_existing_thread_mail(...)`
   - 继续沿 `_send_current_status_query(...)` 产出 canonical `[STATUS]` mail
3. 保持当前 mail path 语义不变：
   - running session 仍走 current running summary / reply 逻辑
   - non-running session 仍走 current non-running status 逻辑
4. 不在此工作包里引入：
   - targeted-session `/status`
   - cross-workspace `/status`
   - direct-only status body

### Done When

- loopback / runtime 测试里，accepted direct `/status` 能稳定产出 canonical `[STATUS]` mail
- subject、state capsule、reply 内容读法与 mail path 一致
- packet ack / error code 与 shared contract 一致

## WP3：再落 direct `reply`

### 目标

在不放大 scope 的前提下，让第一批 `current-session plain reply` 复用现有 canonical reply continuation 路径。

### 代码面

- `mail_runner/relay_server/post_creation_actions.py`（新）
- `mail_runner/session_action_bridge.py`（新）
- `mail_runner/app.py`
- `mail_runner/intent_parser.py`
- `tests/test_relay_server_direct_actions.py`
- `tests/test_relay_server_runtime.py`
- `tests/test_intent_parser.py`
- 相关 app-level reply tests

### 工作内容

1. 新增 `action = reply` packet validator：
   - `reply.reply_text` 必须是非空 plain text
   - v1 不接受 attachments
2. 在 bridge 前先做 first-slice scope gate：
   - 拒绝 quick answer specialization
   - 拒绝 multi-question `Answers:`
   - 拒绝 paused `/resume`
   - 拒绝 attachment continuation
   - 若当前 session 处于必须走 answer / resume 的状态，也不要静默降级成别的行为
3. scope gate 通过后：
   - synthesize ingress mail body 为 `reply.reply_text`
   - 通过 bridge seam 进入 `_process_existing_thread_mail(...)`
   - 让现有 parser / compiler / runner 继续决定是 accepted、status reply、question reply、还是 no-op status
4. accepted 后继续保持当前 canonical user-visible结果：
   - `[ACCEPTED]`
   - `[RUNNING]`
   - `[QUESTION]`
   - `[PAUSED]`
   - `[DONE]`
   - `[FAILED]`
   - `[KILLED]`

### Done When

- first-slice plain `reply` 可以 bridge 到现有 canonical reply ingress
- out-of-scope reply 变体会稳定返回 hard-stop，而不是被误吃成普通 continuation
- accepted 后的后续 status mail / terminal mail 仍沿当前 mail path 产出

## WP4：冻结 classification

### 目标

把 post-creation action 的 machine-readable 分类在仓库代码里做成显式 helper，而不是散落在 handler 分支里。

### 代码面

- `mail_runner/relay_server/post_creation_actions.py`（新）
- `tests/test_relay_server_direct_actions.py`

### 工作内容

至少固定三层读法：

- `fallback_required`
  - `unsupported_action`
  - `direct_temporarily_unavailable`
- `hard_stop`
  - `invalid_payload`
  - `validation_failed`
  - `unauthorized`
  - `session_identity_unresolved`
  - `session_identity_mismatch`
  - `current_session_only_violation`
- `switch_blocker`
  - 不放在 packet ack code 里伪装成普通 reject
  - 继续作为 parity / closeout 严重度，在 accepted 后的 evidence 阶段判断

仓库侧应提供一个与 `classify_direct_new_task_server_outcome(...)` 对应的 post-creation classifier，避免 Android 侧后续继续猜错误语义。

### Done When

- packet-level success / error message 能被稳定投影到 `fallback_required` 或 `hard_stop`
- `switch_blocker` 只出现在 closeout / parity 判断，不混入 ack-level classifier

## WP5：补齐 closeout 工件

### 目标

让 post-creation direct action 在 accepted 后也能留下与 shared contract 对齐的 closeout 证据，而不是只靠翻 raw mail。

### 代码面

- `mail_runner/canonical_run_summary.py`
- `mail_runner/taskmail_closeout.py`
- 推荐新增 `mail_runner/session_action_closeout.py`
- `tests/test_taskmail_closeout.py`
- 相关 relay direct-action tests

### 工作内容

1. 对 accepted direct `reply`：
   - 继续保留 run-scoped `canonical_summary.json`
   - 补充可选字段：
     - `action_type`
     - `target_session_identity`
2. 对 accepted direct `/status`：
   - 不能强行复用 run-scoped `canonical_summary.json`
   - 推荐新增 thread-scoped action 工件，例如：
     - `tasks/<thread_id>/session_actions/<request_id>/session_action_closeout.json`
3. 无论 `reply` 还是 `/status`，closeout 至少保留：
   - `action_type`
   - `target_session_identity`
   - `request_id`
   - `ingress_message_id`
   - `terminal_mail_subject`
   - `last_summary`
   - `same_run_bind`
4. `same_run_bind` 字段名继续沿用当前 closeout 读法：
   - 对 `reply`，它仍然是同 run bind
   - 对 `/status`，它仍然保留同名字段以保持 closeout 结构稳定，即使这次 action 不一定启动新 run
5. `taskmail_daily_closeout_bundle.json` 后续应能同时读取：
   - run-scoped `canonical_summary.json`
   - 或 post-creation action closeout 工件

### Done When

- accepted direct `reply` 与 accepted direct `/status` 都能产出 machine-readable closeout 工件
- closeout 工件里的字段名与 shared contract 保持一致
- `taskmail_daily_closeout_bundle.json` 不再只能解释 direct `new_task`

## WP6：测试与证据顺序

### 推荐测试门槛

1. 先跑 resolver / classifier / packet validation 的 targeted tests
2. 再跑 direct `/status` loopback / runtime tests
3. 再跑 direct `reply` loopback / runtime tests
4. 再跑 closeout / bundle tests
5. 若共享 runtime path 被改动，再跑全量 `.\.venv\Scripts\python.exe -m pytest`

### 推荐优先测试文件

- `tests/test_relay_server_direct_actions.py`
- `tests/test_relay_server_runtime.py`
- `tests/test_app_phase3.py`
- `tests/test_intent_parser.py`
- `tests/test_taskmail_closeout.py`

### live 证据建议

只有在本地 targeted tests 与全量回归都解释清楚后，才建议进入 live closeout。第一轮 live 证据应优先收两条：

1. current-session direct `/status`
2. current-session plain direct `reply`

这两条 live closeout 都至少应带出：

- Android retained send evidence
- relay packet ack / packet store
- PC ingress raw mail
- terminal status mail
- closeout 工件

## 文档回写顺序

在下列条件同时满足前，不应改写 `docs/current/*`：

- direct `/status` 已有稳定 accepted path 与 closeout 工件
- direct `reply` 已有稳定 accepted path 与 closeout 工件
- out-of-scope `reply` 变体已经有稳定 hard-stop 证据
- 至少一轮 live closeout 已能解释 `same_run_bind`

满足上述条件后，才考虑依次回写：

1. `docs/current/mail_protocol.md`
2. `docs/current/android_runner_communication_contract.md`
3. `docs/current/README.md`

## 明确不做

本计划当前明确不做：

- 把 quick answer / `Answers:` / `/resume` / attachment continuation 混进第一批
- 把 targeted-session variant 拉进第一批
- 把 cross-workspace switching 拉进第一批
- 把 direct `reply` / direct `/status` 直接提升为 Layer 1 事实
- 因为这条实现计划而顺手扩写 `phase2_direct_outbound_contract_v1.md`
- 因为 post-creation action 落地而提前宣告 `direct-default`

## Exit Reading

当这份计划进入“可收口”状态时，仓库侧至少应满足：

- `/status` 与 `reply` 都已经有 repo-side implementation note 对应的 landed code 和 targeted tests
- post-creation classifier 已经稳定
- closeout 工件已能覆盖两类 action
- Android 不再需要猜 packet schema、fallback 语义、或 closeout 字段
- `docs/current/*` 是否升级，取决于实现与证据，而不是取决于 planning 文件是否存在

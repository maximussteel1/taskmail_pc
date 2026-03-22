# Phase 5 Long-Term Fallback Note

## Status

- Date: 2026-03-22
- Scope: repository-side first draft baseline for the shared Phase 5 long-term fallback note
- Layer: Layer 2 shared artifact draft
- Related docs:
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/phase4_dual_stack_parity_plan.md`
  - `docs/plans/phase4_dual_stack_parity_checklist.md`
  - `docs/plans/phase4_mismatch_ledger.md`
  - `docs/plans/phase4_rollback_trigger_note.md`
  - `docs/plans/phase5_long_term_default_hardening_plan.md`
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/README.md`

## Status Reading

- 本文已从 skeleton 升级为首版 draft，但仍处于 Phase 5 `pre-freeze` 阶段。
- 本文不宣告 Phase 4 已收口，也不宣告 `direct-default` 已经切换完成。
- 本文当前只面向 `new_task` covered flow，不把 `reply`、`/status` 或更广的 direct control path 提前并入。
- 本文当前写的是“长期 direct-default 之后 fallback 至少要如何继续真实存在”的 shared 起点，而不是最终 freeze 版本。

## 当前起点

- Phase 4 已形成 parity checklist、mismatch ledger、rollback trigger note 三份共享工件的首轮仓库基线。
- `new_task` 的 repo-side outcome normalization 已经存在：accepted、fallback-classified rejection、hard rejection 现在共用一个 classifier。
- `new_task.direct_accepted`、`new_task.fallback_to_mail` 与 `new_task.hard_rejection_stop` 当前 repo-side 读数都已是 `pass`。
- `canonical_summary.json` 已提供 `ingress_type`、`request_id`、`ingress_message_id`、`last_summary`、`terminal_mail_message_id`、`terminal_mail_subject` 等同 run 对账锚点。
- post-accept failure 当前已可在 packet store 中持久化 `last_error_code`，不再只靠错误文本做 fallback / rollback 判断。
- canonical `mail new_task` 基线仍是当前 user-visible truth layer。
- 当前尚未形成“direct path 已是 covered flow 默认主路径”的 shared closeout 结论。

## 本文预期职责

- 记录当 direct path 成为 covered flow 默认主路径后，mail fallback 仍必须保持为真实可运行路径，而不是只留在文档里的名义兜底。
- 记录 bootstrap failure、fallback-classified rejection、post-accept failure、mail path unavailable 这几类结果在长期运行口径下应如何处理。
- 记录 operator-facing 与 user-facing 的 fallback 行为，不把 fallback 退化成隐藏 dead code。
- 给后续 Android / PC / VPS shared review 一个稳定问题清单，避免把 Phase 4 未闭环问题误写成“Phase 5 已接受现实”。

## 首版冻结边界

当前 draft 只预留以下边界：

- `new_task` covered flow 下的长期 fallback 语义
- direct default 之后仍需保留的 mail fallback 生存要求
- fallback 相关 evidence、operator 动作与用户可见结果
- direct accepted 之后不得伪造 fallback、hard rejection 不得隐式降级的边界

当前明确不写入：

- direct `reply`
- direct `/status`
- token 轮换细节
- reconnect / stale-session / replay 细节
- 删除 mail fallback
- Android UI 交互重构细节

## 长期 fallback 口径初稿

### 1. fallback 是长期能力，不是过渡期装饰

- 只要 `new_task` 的 direct path 还可能因为 bootstrap、capability、transport、或环境不稳定而失败，mail fallback 就必须继续保持可运行。
- 即便未来 `direct-default` 已经切换，fallback 也不能退化成“文档里写着存在，但没有 smoke / regression 证据”的假路径。
- 对仓库侧来说，长期 fallback 的最低证明标准仍然包括 repo-side machine-readable evidence，而不只是人工日志阅读。

### 2. `packet_ack.accepted = true` 后禁止 duplicate mail fallback

- 这条边界继续沿用 Phase 2 / Phase 4 冻结口径。
- 一旦 direct packet 已被服务端 accepted，就不能再静默补发一条 mail `new_task` 去“兜底”。
- accepted 之后如果后续 outcome 出现问题，应进入 mismatch / rollback 评审，而不是伪装成合法 fallback。

### 3. fallback-classified rejection 继续指向 mail `new_task`

- `unsupported_action`、bootstrap failure、transport failure、`direct_temporarily_unavailable`，以及等价的 fallback-classified rejection，仍应允许导向当前 canonical mail `new_task` 路径。
- 这类场景下用户最终看到的应是 fallback 后的 canonical 成功语义，而不是虚假的 direct accepted。
- Android 侧是否实际重新发起 mail `new_task` 仍是客户端动作；PC / VPS 仓库当前负责冻结的是分类边界、证据形状、以及“不接受把 fallback 写成空名义”的规则。

### 4. hard rejection 不是 fallback 入口

- `invalid_payload`、`validation_failed`、`unauthorized` 及等价 hard rejection 不得静默降级成 mail fallback。
- 这类场景下客户端应保留 draft，并明确展示 direct-send error，而不是伪造成功。
- 如果未来要改变这条边界，必须先有新的 cross-repo contract freeze，而不是在 Phase 5 文档里偷偷改口。

### 5. post-accept failure 属于 rollback / mismatch 处理，不属于隐式 fallback

- direct accepted 后再出现 delivery 失败、summary 缺失、terminal outcome 漂移，不能直接改写成“其实应该 fallback”。
- 这类情形必须至少形成 packet store、runtime state、terminal mail / summary 三类证据中的可绑定组合，再进入 rollback review。
- 只有当双方明确同意某种 accepted-after-failure 的恢复策略，后续 freeze 才能把它写成长期默认规则；在此之前，它仍属于需要审慎记账的高风险区。

## Evidence Consumption Order

长期 fallback 相关对账，当前建议固定按以下顺序消费证据：

1. 优先读同 run 的 `canonical_summary.json`
2. 再读 packet store、outbound journal、runtime `thread_state.json`
3. 最后按 `terminal_mail_message_id` 或 terminal status mail 类型定位 `mail/raw_*.json`

当前不允许把固定编号的 `raw_004.json` 写成 fallback / terminal outcome 的稳定锚点。

对同 run 样本，至少应能解释这些 join 关系：

- `request_id`
- `ingress_message_id`
- `terminal_mail_message_id`
- `last_summary`
- `last_error_code`，如果这是 post-accept failure 样本

但 `hard_rejection_stop` 不是 accepted-path same-run closeout：

- 它不要求 `canonical_summary.json`、`taskmail_daily_closeout_bundle.json`、或 `request_id` bind 才能关单
- 当前 shared closeout 口径固定为：
  - relay hard rejection evidence
  - Android `local stop + draft retention`
  - `no implicit mail fallback`
  - `thread_085` 之后无新的 adjacent-runtime thread
- 因此它不应再被重写成“还缺一个正路径 bundle / bind closeout”的 open tail

## Draft Table

| fallback_case_id | covered_flow | trigger | allowed_action | expected_user_visible_result | required_repo_evidence | current_repo_readout | freeze_gap |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `long_term_fallback.bootstrap_failure` | `new_task` | direct bootstrap 未达到可发送前置状态 | 允许 fallback 到 canonical mail `new_task`；记录 transport / bootstrap 失败证据 | 用户看到 canonical mail 成功语义，或在 mail 也失败时看到真实失败 | bootstrap probe、failure receipt、canonical summary 或对应 status/mail evidence | `same_run_positive_sample_present` | `thread_093` bootstrap-unavailable same-run 正样本已存在；fallback row 当前不再单列 workflow-reuse / automatic-bind blocker |
| `long_term_fallback.ack_fallback_classified_rejection` | `new_task` | 收到 `unsupported_action`、`direct_temporarily_unavailable` 或等价 fallback-classified rejection | 允许 fallback 到 canonical mail `new_task`；不得伪装成 direct accepted | 用户看到 fallback 后 thread / status / summary，与 mail baseline 一致 | `packet_ack` / error code、packet store、canonical summary、Android retained evidence | `same_run_positive_sample_present` | fallback row 的 same-run 正样本已存在（`thread_093` / `thread_094`）；`thread_097` / `thread_098` 已补齐 shared bundle workflow reuse 与 `request_id`-first bind authority，因此这行当前不再单列 automatic-bind blocker |
| `long_term_fallback.hard_rejection_stop` | `new_task` | 收到 `invalid_payload`、`validation_failed`、`unauthorized` 或等价 hard rejection | 不允许隐式 mail fallback；保留 draft 并停止自动发送 | 用户看到真实 direct-send error，而不是成功 outcome | reject classification、no-new-thread evidence、Android hard-rejection evidence | `shared_row_pass` | 当前按 negative closeout 读取：relay hard rejection evidence + Android `local stop + draft retention` + `no implicit mail fallback` + `thread_085` 之后无新 adjacent-runtime thread；不要求 `daily_closeout_bundle` / `request_id` bind |
| `long_term_fallback.post_accept_failure` | `new_task` | direct accepted 后 delivery / summary / terminal outcome 出现失败或漂移 | 不允许 duplicate mail fallback；升级到 mismatch / rollback review | 用户不得看到伪造成功；必要时阻止 `direct-default` 扩大 | packet store `last_error_code`、runtime state、canonical summary、terminal mail evidence | `repo_side_evidence_present` | 缺 shared 恢复动作与同 run closeout 口径 |
| `long_term_fallback.mail_path_unavailable` | `new_task` | direct 失败后，mail fallback 也失败 | 保留 draft，展示真实错误，阻止扩大 `direct-default` | 用户看到真实失败；operator 能定位 direct 失败与 mail 失败的双重证据 | outbound journal、mail send failure evidence、Android terminal evidence | `repo_side_boundary_defined` | 缺 Android / PC 联合 smoke 证明这条路径未退化 |

## 当前仓库侧读数

### F1 Bootstrap Failure / Ack-Level Fallback

- Phase 2 fallback matrix 已冻结：bootstrap failure、transport failure、`unsupported_action`、`direct_temporarily_unavailable` 仍属于 fallback-classified rejection。
- Android 当前 shared readout 已把 `new_task.fallback_to_mail` 记为 `pass`，并已有 `thread_093` / `thread_094` 两条 fallback-row same-run 正向样本。
- 因此，Phase 5 当前不应再把 fallback row 写成“same-run 样本缺失”，也不应再把 automatic bind blocker 挂在 fallback row 上；更准确的 current reading 是 `thread_097` / `thread_098` 已把 shared bundle workflow reuse 与 `request_id`-first bind authority 关掉。

### F2 Hard Rejection Stop

- Phase 4 parity checklist 已明确：hard rejection 当前 repo-side 读数是 `pass`，Android 当前 shared readout 也已把 `new_task.hard_rejection_stop` 记为 `pass`。
- 这意味着 PC / VPS 与 Android 侧都已确认 hard rejection 分类、draft retention、以及 no-implicit-fallback 语义没有漂移。
- 当前正确读法不再是“还缺一个 ack-level hard rejection live closeout”；更准确的 shared authority 是这条 row 已按 negative closeout 关单，不再要求 accepted-path bundle / bind 证据。

### F3 Post-Accept Failure

- Phase 4 rollback trigger note 已明确把 post-accept failure 读作 `accepted_without_expected_outcome`、`summary_outcome_drift`、`fallback_path_unavailable` 等阻塞条件，而不是合法 fallback。
- 仓库侧现已具备 packet store `last_error_code`、runtime state、mail outcome evidence 这类 machine-readable readout。
- 因此，Phase 5 当前应把 post-accept failure 归入 rollback / mismatch 处理，并明确写死“accepted 后不能再补发 duplicate mail fallback”。

## 当前阻塞点

- Phase 4 的 cross-repo 三场景矩阵还未形成 shared closeout。
- `direct-default` 尚未被双方共同确认为 covered flow 的正常主路径。
- fallback row 的 same-run positive sample 已有；`thread_097` / `thread_098` 也已把 shared bundle workflow reuse 与 `request_id`-first bind authority 关掉，因此当前剩余 open tail 不再挂在 fallback row 上。
- hard rejection row 已有 shared `pass` 读数，并且当前应继续按 negative closeout 读取；后续若 current build 再出现 regression，应退回 Phase 4 mismatch / rollback，而不是把这条已闭环 row 长期保留为 standing blocker。
- `mail_path_unavailable` 目前仍只有 repo-side failure evidence，缺双边 smoke 证明。

## Exit Reading

这份 note 可以进入 freeze，至少要满足：

- Phase 4 covered flow 已形成 cross-repo closeout。
- `new_task` 的 `direct-default` 边界已经被双方确认。
- bootstrap failure / fallback-classified rejection / hard rejection / post-accept failure 至少都已有一致的 shared 解释。
- fallback 不是只停留在文档里，而是仍有真实 smoke 或回归证据。
- Android 与 PC / VPS 都能对“什么时候 fallback、什么时候不 fallback、什么时候必须阻塞 switch”给出一致解释。

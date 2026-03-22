# Phase 5 Token And Reconnect Handling Note

## Status

- Date: 2026-03-22
- Scope: repository-side first draft baseline for the shared Phase 5 token and reconnect handling note
- Layer: Layer 2 shared artifact draft
- Related docs:
  - `docs/plans/android_pc_vps_coordinated_execution_plan.md`
  - `docs/plans/phase1_direct_connect_bootstrap.md`
  - `docs/plans/phase2_direct_outbound_contract_v1.md`
  - `docs/plans/phase3_direct_inbound_wire_v1.md`
  - `docs/plans/phase3_direct_inbound_fixture_package_v1.md`
  - `docs/plans/phase4_dual_stack_parity_plan.md`
  - `docs/plans/phase4_dual_stack_parity_checklist.md`
  - `docs/plans/phase5_long_term_default_hardening_plan.md`
  - `docs/plans/phase5_long_term_fallback_note.md`
  - `docs/plans/vps_relay_deploy_runbook.md`
  - `docs/current/android_runner_communication_contract.md`
  - `docs/current/README.md`

## Status Reading

- 本文已从 skeleton 升级为首版 draft，但仍处于 Phase 5 `pre-freeze` 阶段。
- 本文不改变当前 direct scope，也不授权把 `reply`、`/status` 或其他 direct control flow 提前纳入默认路径。
- 当前首轮优先仍是 `new_task`，并只把 Phase 3 active-session detail sidecar 当作 reconnect 讨论里的辅助 read-side 输入。
- 本文当前写的是“长期 direct-default 之后 token / reconnect / replay 至少要如何被一致解释”的 shared 起点，而不是最终 freeze 版本。

## 当前起点

- Phase 1 已把 direct bootstrap 的 `hello_ack`、`unauthorized`、`token_id_mismatch`、`connect_failure`、`timeout` 等状态词汇冻结成可审阅分类。
- Phase 2 已冻结 `packet_id` / `request_id` 的 idempotency 规则、`receipt_id` 稳定性、以及 `unauthorized` / `invalid_payload` / `validation_failed` 属于 hard rejection 的 send-side 边界。
- Phase 3 已冻结 active-session detail sidecar 的 subscribe / snapshot / delta / gap / resubscribe 规则，并已有 `gap_resubscribe_fresh_snapshot` 这类 fixture 作为 reconnect 恢复样本。
- 当前 Layer 1 仍明确要求：Android 不得把 relay token 当成通用应用协议凭证使用，mail / local-cache 仍是 active detail 的 truth / fallback 层。
- `mail_runner.observe` 当前已有 `normal`、`stale`、`suspected_stuck`、`orphaned` 这层 runtime health 读数，但它们仍属于 operator-facing 诊断层，不是新的 direct wire contract。
- accepted-path 的 `request_id`-first same-run authority 现已由 Android `thread_097` / `thread_098` 关闭，因此本文不应回头重开 bind ladder，也不应把 replay / idempotency 的剩余 freeze gap 再写回 accepted-path closeout 缺口。
- 当前尚未形成“direct path 已是 covered flow 默认主路径”的 shared closeout 结论，因此本文仍只能停在 `pre-freeze` draft。

## 本文预期职责

- 说明长期运行口径下 transport token 如何被持有、轮换、失效和诊断。
- 说明 direct path 默认化后，bootstrap reconnect、session detail resubscribe、stale-session、以及 replay / idempotency 应如何解释。
- 说明哪些 token / reconnect / replay 场景是 shared protocol boundary，哪些仍只是 operator runbook 或实现细节。
- 给后续 Android / PC / VPS shared review 一个稳定问题清单，避免把尚未冻结的 token / reconnect 现实混写成“默认路径已经长期硬化完成”。

## 首版冻结边界

当前 draft 只预留以下边界：

- transport token 的长期持有与轮换期望
- bootstrap admission failure 之后的 reconnect / retry 边界
- `unauthorized` 在 bootstrap 与 business packet 两层的不同语义
- `packet_id` / `request_id` 的 replay 与 idempotency 边界
- active-session detail sidecar 在连接丢失、gap、apply failure 下的 resubscribe / stale-session 语义

当前明确不写入：

- Android 凭证存储实现细节
- VPS 之外的更广 token 分发体系
- direct `reply`
- direct `/status`
- 全量 workspace / history API
- Android UI 交互重构细节

## 长期 token / reconnect 口径初稿

### 1. transport token 仍是狭义 operator-provisioned capability

- relay transport token 当前仍只服务于狭义、operator-provisioned 的 Phase 2 / Phase 3 direct scopes，而不是通用 Android 应用协议凭证。
- Android 当前不得在这些狭义 scope 之外存储或使用通用 relay transport token；Phase 5 也不借本文放宽这条边界。
- `healthz.auth.transport_token_id` 继续只作为 operator-facing 诊断锚点，而不是 app-facing business identity。
- 本文当前冻结的是 token ownership / exposure boundary，不冻结 Android 端或 VPS 端的具体密钥存储实现。

### 2. `unauthorized` 必须区分 bootstrap admission failure 与 business hard rejection

- 如果 `hello` 阶段返回 `unauthorized` 或等价 admission rejection，这应被读取为 direct path misconfigured / unavailable，而不是“direct path 正常但暂时拥堵”。
- 这类 bootstrap 级 `unauthorized` 应按 Phase 1 / Phase 5 fallback 口径处理：direct path 不可用，用户可继续留在 canonical mail 路径。
- 如果 business packet 已发送且返回 `unauthorized`、`invalid_payload`、`validation_failed` 或等价 hard rejection，这应按 Phase 2 / Phase 5 fallback 口径处理：保留 draft、显示 direct-send error、不得静默降级成 mail fallback。
- 长期文档里不得把这两类 `unauthorized` 混成同一类 transient failure，否则会直接破坏 Phase 2 / Phase 4 的 fallback 边界。

### 3. bootstrap reconnect 是有界 retry，不是无限隐式重试

- `healthz` 继续只是诊断入口，不是 direct business truth；只有 `hello_ack` 才表示 direct bootstrap path 当前可用。
- bootstrap 失败后的 reconnect / retry 只能被解释为“同一次可见 direct-send 尝试内的有界重试”，不能演变成无限期静默自旋。
- 一旦 Android 已对用户显示 direct-send failure，本次尝试就应视为结束；后续 fresh user tap 才可以创建新的 user-visible attempt。
- 这意味着 operator 必须能够通过 bootstrap probe result、failure receipt、或等价 machine-readable evidence 解释失败发生在 retry 前、retry 中、还是 failure surface 之后。

### 4. `packet_id` / `request_id` 是 replay 与 idempotency 的主锚点

- relay-level retry 在用户尚未看到失败前，应继续复用同一组 `packet_id` 与 `request_id`。
- 对 PC / VPS 来说，重复收到相同 `packet_id` 的 packet 仍应被视为同一 logical direct request；`receipt_id` 应保持稳定。
- `packet_ack.accepted = true` 仍只表示 direct request 已被 accepted 进 direct lane，不等于 session 已创建完成，也不授权 Android 从 ack 直接推断最终 session identity。
- 只有在用户已被明确告知失败、且发生新的 fresh tap 时，才允许创建新的 `request_id` 并进入新的 logical attempt。

### 5. active detail sidecar 的 reconnect / resubscribe 继续服从 snapshot-first 与 mail fallback

- active-session detail sidecar 仍只用于 freshness；mail / local-cache 继续是 receipt、history、以及 gap / reject 时的 truth / fallback 层。
- accepted subscribe 之后，server 仍必须先发 `session_snapshot`，不得先发 `session_delta`。
- 若 Android 检测到 gap，或本地无法安全应用 delta，不应猜测修补；应保留当前 mail/local-cache detail，并重新 subscribe 请求新 snapshot。
- 当前 Phase 3 已冻结 `detail_open`、`detail_refresh`、`detail_reconnect` 三类 reason vocabulary；Phase 5 当前只把它们解释成 reconnect / refresh intent 的稳定命名，不扩写为新的 direct control API。
- `gap_resubscribe_fresh_snapshot` fixture 已经说明：gap 之后的正确恢复路径是 resubscribe + fresh snapshot，而不是盲目继续吃后续 delta。

### 6. stale-session 是诊断与恢复边界，不是新的 direct truth layer

- 当前 runtime health 的 `stale`、`suspected_stuck`、`orphaned` 读数，帮助 operator 解释“这个 active session 现在是否看起来失联或无进展”，但它们本身不是 direct wire enums。
- 对 Android 来说，stale-session 不能被解释成“mail truth 已失效”；只要 direct sidecar 不可靠，mail / local-cache 仍必须保留为 detail truth。
- 若 server 无法安全从 `last_known_sequence` 继续恢复，必须发新的 snapshot；不得把 sequence 回退到更小值，也不得伪造 continuity。
- 如果 canonical session identity 已不可安全恢复，Phase 5 当前更倾向于把它留在 mail/local-cache 解释层，而不是偷偷扩写成新的 session repair API。

## Evidence Consumption Order

token / reconnect 相关对账，当前建议固定按以下顺序消费证据：

1. 优先读 Phase 1 / Phase 2 / Phase 3 已冻结的 contract / wire / fixture 文档
2. 再读 bootstrap probe result、failure receipt、`packet_ack`、packet store、runtime state
3. 对 active detail sidecar，再读 Phase 3 fixture package 与 runtime health readout
4. 最后才读 operator runbook，例如 `healthz.auth.transport_token_id` 这类诊断锚点

当前不允许跳过 contract freeze，直接把某次临时 operator 操作记成长期 token / reconnect 规则。

## Draft Tables

### Token Topics

| topic_id | scope | trigger_or_question | expected_long_term_rule | required_repo_evidence | current_repo_readout | freeze_gap |
| --- | --- | --- | --- | --- | --- | --- |
| `token.transport_token_ownership` | direct bootstrap | transport token 由谁配置、谁暴露、谁消费 | 保持 operator-provisioned 窄 scope；不升级成通用 app token | Phase 1 bootstrap note、current Android contract、deployment runbook | `repo_side_boundary_defined` | shared operator ownership 仍未 freeze |
| `token.rotation_overlap` | direct bootstrap | token 轮换时是否允许 overlap window | overlap 或 cutover policy 必须显式、可审阅、可诊断 | operator runbook、deployment procedure、bootstrap behavior note | `question_open_pre_freeze` | rotation policy 尚无 shared freeze |
| `token.unauthorized_surface` | bootstrap + business packet | `unauthorized` 如何区分 misconfigured admission 与 hard rejection | admission failure 与 business hard rejection 必须分开解释，不得混成 transient failure | Phase 1 status vocabulary、Phase 2 fallback matrix、Android retained evidence | `repo_side_contract_present` | Android / PC shared surface 仍缺 closeout |

### Reconnect / Replay Topics

| topic_id | scope | trigger_or_question | expected_long_term_rule | required_repo_evidence | current_repo_readout | freeze_gap |
| --- | --- | --- | --- | --- | --- | --- |
| `reconnect.bootstrap_retry` | `new_task` | bootstrap 失败后的 retry 到哪里为止 | retry 必须是有界同次尝试行为；visible failure 后新尝试才换新 request | bootstrap probe、transport receipts、fallback note | `repo_side_boundary_defined` | shared retry budget / surface 仍未 freeze |
| `replay.packet_idempotency` | `new_task` | relay retry 与 fresh user tap 何时复用同一 `packet_id` / `request_id` | relay-level retry 复用 id；fresh user tap 才换 request | Phase 2 contract、stable `receipt_id` evidence、loopback tests | `repo_side_pass` | accepted-path same-run closeout 已由 `thread_097` / `thread_098` 补齐；剩余 gap 只在 shared retry budget / user-facing retry surface 仍未 freeze |
| `reconnect.subscription_resubscribe` | active detail sidecar | 连接丢失、gap、apply failure 后如何恢复 | 保持 snapshot-first、gap->refresh、mail/local-cache fallback，不伪造 continuity | Phase 3 wire、fixture package、runtime evidence | `repo_side_contract_present` | cross-repo live smoke 仍不足 |
| `reconnect.stale_session_boundary` | active detail sidecar | stale-session 何时可恢复、何时仅保留 mail truth | runtime health 只做诊断；无法安全恢复时回到 mail/local-cache 解释层 | current health docs、Phase 3 wire、operator evidence | `repo_side_boundary_defined` | 长期 stale-session closeout 仍未 freeze |

## 当前仓库侧读数

### T1 Token Ownership And Exposure

- 当前 Layer 1 已明确：Android 不得在狭义 direct scope 之外存储或使用通用 relay transport token。
- 当前 operator 侧 runbook 已把 `healthz.auth.transport_token_id` 固定成可见诊断锚点，说明仓库侧已经把“token fingerprint 可审阅”这件事写进运行口径。
- 但 token rotation 的 overlap / cutover policy 仍未形成 shared freeze，因此本文当前只能把 ownership / exposure boundary 写清，不能把 rotation policy 写成既定事实。

### T2 Unauthorized Surface Split

- Phase 1 已明确把 `unauthorized` 归入 auth / protocol rejection，而不是 transport success。
- Phase 2 已明确把 business packet 上的 `unauthorized` 归入 hard rejection，不允许静默 mail fallback。
- 因此，仓库侧当前已经具备“bootstrap `unauthorized` != business `unauthorized`”的稳定边界；缺的不是分类，而是 shared operator / user surface closeout。

### T3 Replay And Idempotency

- Phase 2 已明确：relay-level retry 复用同一 `packet_id` / `request_id`，fresh user tap 才换新 request。
- 同一 contract 也已冻结：`receipt_id` 对同一 `packet_id` 的重复 relay handling 应保持稳定。
- Android `thread_097` / `thread_098` 也已把 accepted-path 的 same-run authority 读到 `request_id`-first bind，因此当前不应再把 replay / idempotency 的 freeze gap 写成“还缺 latest same-run closeout”。
- 因此，仓库侧当前已经能把 replay / idempotency 写成 reviewable rule，而不是继续靠实现猜测；剩余要收口的是 shared retry budget 与 user-facing retry surface，而不是 bind ladder 本身。

### T4 Sidecar Resubscribe And Stale-Session

- Phase 3 wire 已冻结：gap -> `detail_refresh` -> 新 subscribe -> fresh snapshot，是当前可审阅恢复路径。
- Phase 3 fixture package 已提供 `gap_resubscribe_fresh_snapshot` 作为 recovery 样本。
- 当前 Layer 1 也已明确：sidecar unavailable / rejected / gapped 时，Android 必须保留 mail / local-cache read path 作为 fallback。
- 因此，仓库侧当前已经能把 reconnect / stale-session 写成“mail truth 不丢、direct freshness 可恢复”的边界；缺的是长期 smoke 与 shared freeze。

## 当前阻塞点

- Phase 4 尚未完成，因此“长期默认路径”前提还没有成立。
- token rotation 的 shared operator ownership 与 overlap / cutover policy 还没有 freeze。
- `unauthorized` 的 Android user-facing surface 与 operator-facing closeout 还没有 shared 最终说法。
- replay / idempotency 的 contract 与 accepted-path same-run authority 已经存在；当前仍未 freeze 的是 retry budget 与 visible retry surface，而不是 `request_id` / `packet_id` 绑定规则。
- reconnect / stale-session 的长期 hardening 还没有足够 cross-repo smoke 证据。
- 当前 direct scope 仍限制在 `new_task` 与 active detail sidecar，更广 reconnect 语义不应提前扩写。

## Exit Reading

这份 note 可以进入 freeze，至少要满足：

- `direct-default` 已经对 covered flow 成立。
- token ownership、rotation policy、以及 `unauthorized` 的双层语义已有一致 shared 说法。
- relay-level retry 与 fresh user tap 的 `packet_id` / `request_id` 边界已有一致 shared 解释。
- reconnect / replay / stale-session 行为已有最小 smoke 或 fixture 证据。
- Android 与 PC / VPS 都能解释“什么时候重连、什么时候只保留 mail truth、什么时候不能假装 continuity”。

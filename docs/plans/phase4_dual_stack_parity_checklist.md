# Phase 4 Dual-Stack Parity Checklist

## Status

- Date: 2026-03-22
- Scope: repository-side first validated matrix baseline for the shared Phase 4 parity checklist
- Layer: Layer 2 shared artifact draft
- Related docs:
  - `docs/plans/phase4_dual_stack_parity_plan.md`
  - `docs/plans/phase2_direct_outbound_contract_v1.md`
  - `docs/current/mail_protocol.md`
  - `docs/current/android_runner_communication_contract.md`
  - `E:\projects\android_task_manager\docs\taskmail\planning\android\phase4_dual_stack_parity_checklist.md`

## Status Reading

- 本文是 PC 侧对 `phase4_dual_stack_parity_checklist.md` 的首轮仓库验证基线。
- 当前仅覆盖 `new_task`。
- 当前已回填并验证 PC 仓库侧可直接证明的 authority / fallback / mail-baseline evidence，包括 `classify_direct_new_task_server_outcome(...)` 和 packet store `last_error_code`。
- 2026-03-22 的 repo-side targeted tests 与 full suite 已通过，但这仍不等同于 Android / PC 双边 parity 已验收。
- 本文不授权把 `reply`、`/status` 或其他 flow 提前纳入 direct-default。

## 当前冻结口径

本 checklist 当前只按以下 authority 字段与结果语义对账：

- `packet_ack.accepted`
- `receipt_id`
- 可选 `transport_message_id`
  - 仅作为观察字段
  - 不能被当作 v1 UI identity 依赖
- fallback-classified rejection
- hard rejection
- 是否产生预期的 thread / mail outcome
- 是否保持与当前 mail baseline 一致的 user-visible summary / terminal outcome

对仓库侧来说，这些 shared evidence 还应结合现有 packet store / outbound journal / runtime state 使用，但不额外扩写为新的跨仓 authority 字段。

## 检查表

| parity_item_id | covered_flow | scenario | expected_direct_evidence | expected_thread_mail_outcome | expected_user_visible_outcome | current_status | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| new_task.direct_accepted | `new_task` | direct accepted | `packet_ack.accepted = true`，存在稳定 `receipt_id`，可选 `transport_message_id` 仅作观察 | 不发送 duplicate mail fallback；后续按当前 canonical mail contract 产生预期 thread / session / status mail | Android 不应提前伪造最终 session id；后续 summary 与 mail outcome 对齐 | `pc_repo_evidence_present` | 见 A1 |
| new_task.fallback_to_mail | `new_task` | fallback-classified direct failure | bootstrap 失败、transport 失败、`unsupported_action`、`direct_temporarily_unavailable`，或其他 fallback-classified direct 结果 | 当前 mail `new task` 路径成功创建预期 thread / mail outcome | 用户看到的是 fallback 后的成功语义，而不是虚假的 direct accepted | `pc_repo_boundary_confirmed` | 见 A2 |
| new_task.hard_rejection_stop | `new_task` | hard rejection local stop | `invalid_payload` / `validation_failed` / `unauthorized` 或等价 hard rejection 分类 | 不产生新的 fallback mail；不产生非预期 thread | draft 保留，客户端明确显示 direct-send error | `pc_repo_evidence_present` | 见 A3 |

## 首轮仓库矩阵读数

| matrix_item | repo_side_readout | notes |
| --- | --- | --- |
| `new_task.direct_accepted` | `pass` | repo-side accepted path、stable `receipt_id`、可选 `transport_message_id`、canonical mail outcome 已有代码与测试闭环；Android authority `thread_097` / `thread_098` 现已补齐 shared bundle workflow reuse 与 `request_id`-first bind 的 closeout |
| `new_task.fallback_to_mail` | `pass` | repo-side rejection classification、post-accept `last_error_code`、mail baseline 均已验证；Android `thread_093` / `thread_094` 已补齐 fallback 实际动作与 same-run closeout，因此这行当前不再承载 workflow / bind blocker |
| `new_task.hard_rejection_stop` | `pass` | repo-side hard rejection 分类与 no-implicit-fallback 语义已验证；Android formal-host negative closeout 也已按 relay hard rejection、local stop、draft retention、以及 `thread_085` 之后无新 adjacent-runtime thread 读成 `pass`；该行不要求 `daily_closeout_bundle` 或 `request_id` bind，因为它不是 accepted-path same-run closeout |

## 稳定消费顺序

- 每次 cross-repo 对账先冻结一个 `sample_thread`、同一 sender-account scope，以及对应 `runs/<task_id>`，不混读跨 run 工件。
- 日常 `daily_closeout_bundle` 至少要同时具备：
  - PC canonical outcome artifact
  - packet store / outbound journal / runtime-state supporting evidence
  - Android latest send evidence
  - Android terminal-summary artifact
- 当前 repo-side 的最小自动化入口已落到 `scripts/build_taskmail_closeout_bundle.py`：
  - 输入 `thread_id` 与可选 `--android-send-records` / `--sender-account-id` / `--android-last-summary`
  - 输出同 run 的 `taskmail_daily_closeout_bundle.json`
  - 固定执行 `request_id` -> `transport_message_id <-> ingress_message_id` -> `last_summary` 的 bind readout
  - 当 Android retained record 缺 `request_id` 时，bundle 会继续按 `ingress_type` 推断 expected outcome family，并在同 `repo_path/workdir` 候选里选时间最近的一条；这一步是 Android evidence 的 record selection precedence，不是新的 bind ladder，因此 fallback 行不会再误吸同 workspace 的更新 direct sample，但 `same_run_bind.effective_bind_level` 仍只会落到 `request_id` / `transport_message_id` / `last_summary`
  - 若 `canonical_summary.json` 缺失，则回退到 `thread_state.json`、ingress raw mail 与 terminal raw mail 继续组装 bundle；`outbound/delivery_attempts.jsonl` 与可选 relay packet store 作为 supporting evidence 一并带出
- 若同 run 已有 `canonical_summary.json`，优先先读它的 `ingress_type`、`request_id`、`ingress_message_id`、`last_summary`、`terminal_mail_message_id`、`terminal_mail_subject`；repo-side `thread_state.json`、packet store，以及按 `terminal_mail_message_id` 或 terminal status mail 类型定位到的 `mail/raw_*.json` 只作为补充核对。
- 对 `direct accepted` 行，若同 run 已有 `canonical_summary.json`，`mail/raw_001.json` 只再用来补看 `X-TaskMail-Direct: 1`、`X-TaskMail-Relay-Request-Id` 与 ingress headers，不再作为 primary join source。
- `fallback_to_mail` 行若已有 `canonical_summary.json` 也优先消费它；只有缺该工件时，才回退到 `thread_state.json` 加上按 terminal status mail 类型定位到的 `mail/raw_*.json`，而不是写死 `raw_004.json`。
- repo-side tests、classifier、packet store 证据定义的是 expected behavior，不单独等于 same-run parity `pass`；真正的 same-run 正向样本仍要和 Android retained evidence 一起读。
- 对 `direct accepted` 行，same-run bind 顺序固定为：`request_id` -> `transport_message_id <-> ingress_message_id` -> `last_summary token` 弱回退；若 bind 还没闭环，则该样本继续留在 checklist 备注区，不直接升 mismatch。
- 对 `hard_rejection_stop` 行，不要求 `daily_closeout_bundle` 或 same-run bind。它是 negative closeout：以 relay hard rejection evidence、Android `local stop + draft retention`、`no implicit mail fallback`，以及 `thread_085` 之后无新 adjacent-runtime thread 关单。
- 当前 shared authority 样本可分层读取：`thread_093` / `thread_094` / `thread_095` 仍适合作为 backfill / helper evidence；`thread_097` 已关闭 fresh `daily_closeout_bundle` workflow reuse，`thread_098` 已关闭 `request_id` 首键 bind 的 fresh formal-host gate。

## 仓库侧首轮证据摘录

### A1 Direct Accepted

- `tests/test_relay_server_loopback.py::test_loopback_server_accepts_authenticated_hello_and_packet_idempotently` 证明相同 `packet_id` 的 `receipt_id` 稳定，可作为 Phase 4 parity 的 authority 字段。
- `tests/test_relay_server_direct_actions.py::test_direct_new_task_packet_is_accepted_and_reuses_mail_task_start_path` 证明 direct `new_task` 被接受后，packet `delivery_status = delivered`，thread 状态进入 `done`，`session_name` 与 canonical mail 路径一致，并沿当前邮件基线发出 `[ACCEPTED]` / `[RUNNING]` / `[DONE]`。
- 同一测试现在也覆盖 `classify_direct_new_task_server_outcome(...) == accepted`，保证 Phase 4 不再靠零散 `packet_ack` 字段人工理解 accepted 语义。
- `tests/test_relay_server_direct_actions.py::test_direct_new_task_bridge_sends_canonical_first_mail_without_system_header` 证明 direct bridge 路径可以返回可选 `transport_message_id`，同时保留 `X-TaskMail-Direct`，且不会把该字段抬升为新的 UI identity authority。

### A2 Fallback To Mail

- `docs/plans/phase2_direct_outbound_contract_v1.md` 的 fallback matrix 已冻结：`unsupported_action`、bootstrap / transport 类失败、`direct_temporarily_unavailable` 都应回退到当前 mail `new task` 路径；`packet_ack.accepted = true` 时禁止 duplicate mail fallback。
- `tests/test_relay_server_direct_actions.py::test_direct_packet_returns_unsupported_action_for_non_new_task_phase2_payload` 证明 capability rejection 仍被归类为 `unsupported_action`，且不会在 PC 侧误创建 packet。
- `tests/test_relay_server_direct_actions.py::test_direct_packet_records_post_accept_fallback_classified_failure_on_packet_store` 证明 post-accept `direct_temporarily_unavailable` 现在会在 packet store 留下 `last_error_code`，repo-side fallback-classified rejection 不再只剩错误文本。
- `tests/test_app_phase2.py::test_process_once_runs_new_task_happy_path` 证明 canonical mail `new task` 基线仍能创建 thread，并发出 `[ACCEPTED]` / `[RUNNING]` / `[DONE]`。
- `tests/test_outbound_service.py::test_send_status_update_falls_back_to_email_when_relay_fails` 证明仓库内既有 relay -> email fallback journaling 仍保留可用。
- 需要明确区分：direct failure 后是否由客户端重新走 mail `new task`，仍是 Android 侧动作；当前 shared authority `thread_093` / `thread_094` 已证明 fallback 实际动作与 same-run closeout，PC 仓库此处继续冻结的是分类边界和 mail baseline，而不是改写 Android UI authority。

### A3 Hard Rejection Stop

- `docs/plans/phase2_direct_outbound_contract_v1.md` 已冻结：`invalid_payload`、`validation_failed`、`unauthorized` 属于 hard rejection，不允许静默降级成 mail fallback。
- `tests/test_relay_server_direct_actions.py::test_direct_packet_returns_invalid_payload_for_missing_task_text` 证明 `invalid_payload` 仍会阻止 packet 落库，也不会在 PC 侧制造非预期 thread。
- 同一测试现在也覆盖 `classify_direct_new_task_server_outcome(...) == hard_rejection`，repo-side hard rejection 不再只靠 prose 解释。
- 这条 parity 当前继续依赖 Android 侧保留 draft 并显式展示 direct-send error；PC 仓库侧已确认 hard rejection 分类语义没有漂移，而 shared negative closeout 现已固定读作：relay hard rejection evidence + Android local stop / draft retention + no implicit fallback + 无新增 adjacent-runtime thread。

## Notes

- `current_status` 暂使用：
  - `pc_repo_evidence_present`：PC 仓库已有测试、合同或状态证据，可进入 cross-repo parity 对账。
  - `pc_repo_boundary_confirmed`：PC 仓库已确认分类边界与 mail baseline，但最终 fallback 动作或 UI 表现仍在 Android 侧。
- 如果后续要把新的 flow 纳入 parity checklist，先更新 Phase 4 freeze note 或新增 contract freeze。
- 这份 checklist 的用途是对账，不是重写 protocol authority。

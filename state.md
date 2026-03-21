# Project State

## Current Snapshot

- Updated At: 2026-03-19
- Current Phase: Phase 8 (same-workspace explicit session targeting landed in code)
- Status: Active
- Bootstrap Entry: `.\.venv\Scripts\python.exe -m mail_runner.app --once --config <mail_config.bot.local.yaml>`
- Hosted Loop Entry: `.\.venv\Scripts\python.exe -m mail_runner.host --config <mail_config.bot.local.yaml> --runtime-dir <_tmp_live_mail_runner>`
- Observability Entry: `.\.venv\Scripts\python.exe -m mail_runner.observe --config <_tmp_live_mail_runner\mail_config.loop_30s.yaml> status`
- Test Command: `.\.venv\Scripts\python.exe -m pytest`
- Latest Validation: `.\.venv\Scripts\python.exe -m pytest` -> `272 passed`
- Notes: The codebase now combines thread-based run artifacts with workspace/session scheduler metadata, supports native backend session resume for reply continuation and question-answer recovery, falls back to fresh recovery runs for failed threads without resumable native context, defaults new Codex threads to the SDK transport while keeping legacy CLI records compatible, persists `backend_transport` plus a lightweight `lifecycle=active|ended`, `last_active_at`, and `last_progress_at` on thread/session state, supports explicit `/end` to remove a non-running thread/session from the active working set without rewriting its last run result, derives `normal|stale|suspected_stuck|orphaned` health signals in `mail_runner.observe` using a first-round `300s` threshold, controls the active working set with `max_active_sessions` (default `4`) and auto-ends the least recently active non-running session when that cap would otherwise be exceeded, now uses that same `max_active_sessions` cap as the real cross-workspace background concurrency limit, keeps live mailbox status mails under split retention semantics where only older progress mails (`[ACCEPTED]`, `[RUNNING]`, `[STATUS]`) are replaceable while action-required (`[QUESTION]`, `[PAUSED]`) and receipt (`[DONE]`, `[FAILED]`, `[KILLED]`) mails are retained, keeps Markdown-first status rendering with artifact projection, persists explicit `Permission:` control across snapshot/thread/session with backend-specific highest-permission projection, exposes a first-mail `[SYNC]` project-folder discovery action for `D:\projects` / `E:\projects` or configured roots, recommends a dual-mailbox deployment (`user mailbox` -> `bot mailbox`), consumes inbox mail by IMAP UID increment plus local `UID/Message-ID` dedupe instead of relying on `UNSEEN` alone, now treats IMAP `IDLE` as a best-effort wake-up path for supported bot mailboxes while keeping UID-based polling as the compatibility and truth layer, now bounds `IDLE` reads so a stalled long-lived socket cannot silently freeze the host loop and also forces a full mailbox sync plus `IDLE` rebuild every 5 minutes, now also supports explicit same-workspace session targeting through `/continue <session_id>` plus targeted `/status` / `/pause` / `/resume` / `/end` / `/kill`, routes those targeted results onto the target session's own mail chain, now also supports `/restart-runner` as a local hosted-loop control action that queues a restart request instead of calling the backend, hosts the long-running loop via `mail_runner.host` with a runtime single-instance lock plus `host_state.json`, now makes `manage_mail_runner.ps1` detect and stop same-config legacy `mail_runner.app --loop` leftovers to avoid duplicate mailbox consumption, and now lets that same script schedule an external detached launcher for self-restart so a mail-triggered restart no longer kills the current host inline, preserves automatic status-mail callbacks when restart recovery requeues persisted `accepted` or queued work, externalizes oversized outgoing artifacts to COS presigned links when configured, rewrites COS-delivered APK/IPA object names to `.bin` to avoid default-domain distribution blocking, now forces COS upload through a direct HTTPS client session instead of inheriting ambient proxy env vars, exposes a minimal read-only `mail_runner.observe` CLI for host/thread/queue inspection, persists local `stream.events.jsonl` logs for active `codex + sdk` turns, now lets the SDK sidecar finish a turn immediately after terminal `turn.completed` / `turn.failed` stream events instead of waiting for the Codex CLI child to exit on its own, can now auto-open one focused monitor window per running thread on Windows when `spawn_monitor_windows` is enabled, keeps that window open while the session remains active/resumable, now bounds focused monitor scrollback/startup replay with `monitor_window_buffer_lines` and `monitor_window_history_limit`, and now also accepts local PC-side thread kill requests through `runtime_dir/thread_kill_requests/`, while structured run-result capsules continue to flow into `RunResult` without leaking the machine-readable block into user-visible replies.
- Outbound-doc status: the consumer-facing mail-body contract is now anchored in `docs/current/pc_mail_output_protocol.md` and `docs/current/multimedia_mail_protocol.md`; code alignment is still pending, and the agreed sequencing is `consumer contract freeze -> P9 HTML projection -> broader outbound convergence`.

## Completed Phases

- Phase 0 completed on 2026-03-12.
- Phase 1 completed on 2026-03-12.
- Phase 2 completed on 2026-03-12.
- Phase 3 completed on 2026-03-12.
- Phase 4 completed on 2026-03-12.
- Phase 5 completed on 2026-03-12.
- Phase 6 completed on 2026-03-12.
- Phase 7 first-round delivery aligned on 2026-03-14.

## Confirmed Current Facts

- Runtime artifacts still live under `tasks/thread_xxx/`, and `thread_state.json` remains the primary per-thread state file.
- Scheduler metadata now also lives under `tasks/_scheduler/workspaces/<workspace_id>/`, including `workspace_state.json` and `sessions/<session_id>.json`.
- New non-reply task mail still creates a fresh `thread/session` even when `repo_path`, `workdir`, and subject title match an existing session.
- A first-mail `[SYNC]` request now returns the configured project-root folder inventory without creating a task, runnable thread, or session.
- Reply mail can continue an existing native backend context when `backend_session_id` is available; this is used by plain replies, `/resume`, and answers to `[QUESTION]`.
- Same-workspace explicit session targeting is now available: `/status <session_id>`, `/last <session_id>`, `/continue <session_id>`, `/pause <session_id>`, `/resume <session_id>`, `/end <session_id>`, and `/kill <session_id>` can act on another session in the current workspace and continue on that target session's own mail chain.
- New Codex threads now default to the SDK transport for continuous sessions, while legacy records without `backend_transport` still resolve to `cli`.
- `backend_transport` is now persisted on snapshot/thread/session/run records, and the SDK bridge runs through `scripts/codex_sdk_sidecar/dist/index.js`.
- Active `codex + sdk` runs now persist `runs/<task_id>/stream.events.jsonl`, and `mail_runner.observe show-thread-live <thread_id>` merges that live stream with archived transcript turns for PC-side monitoring.
- Thread/session state now also persists `lifecycle=active|ended` plus `last_active_at` and `last_progress_at`, with legacy records falling back to `updated_at`.
- The active working set is controlled by `max_active_sessions` (default `4`); starting a new task or reactivating an ended thread auto-ends the least recently active non-running session when the cap would otherwise be exceeded.
- `/end` now marks a non-running thread/session as `ended` without rewriting its last run status, and `/resume` or waiting-state continuation can reactivate the same thread back to `active`.
- `mail_runner.observe` now derives `normal`, `stale`, `suspected_stuck`, and `orphaned` using a fixed `300s` threshold; `codex + sdk` live stream timestamps count as progress when newer than persisted state.
- live mailbox retention now only auto-prunes older progress mails; `[QUESTION]`, `[PAUSED]`, `[DONE]`, `[FAILED]`, and `[KILLED]` remain visible as durable action-required or receipt mail.
- `/pause` now moves a non-running thread/session into `paused`; plain replies no longer resume it implicitly, and `/resume` is required to unpause.
- If a paused thread still has a pending question set, `/resume` without an answer restores `[QUESTION]`, while `/resume` with answers continues through the normal answer flow.
- A reply to a `failed` thread without resumable native context now falls back to a fresh recovery run built from the latest saved snapshot, instead of returning a status-only rejection.
- Background scheduling is queue-aware: the same workspace can now run up to `max_active_sessions_per_workspace` sessions concurrently, different workspaces can run concurrently until the shared `max_active_sessions` cap is reached, and follow-up work for a running session is queued on that session.
- Runner restart recovery is implemented for persisted `accepted` and queued work, and recovered runs keep automatic `[RUNNING]` / terminal status mails on the existing reply chain; a leftover persisted `running` task is marked failed unless a queued follow-up can be promoted.
- When `spawn_monitor_windows` is enabled on Windows, the background loop auto-opens one focused monitor window per running thread, shows transcript plus live streamed assistant output when available, keeps that window open while the session remains active/resumable, and bounds the focused window with `monitor_window_buffer_lines` plus `monitor_window_history_limit`.
- PC-side runtime control can now queue a local kill request for a running thread by thread id; the host loop consumes that request from `runtime_dir/thread_kill_requests/` and routes it through the existing backend kill path.
- `Permission:` is now a persisted task/session property: a new task without it uses backend defaults, replies without it inherit current state, and `highest` projects to `Codex` dangerous bypass mode or an `OpenCode` run-scoped permission overlay.
- Real mailbox smoke on 2026-03-16 verified the `Permission:` control path end to end for both backends by email, including status-mail display, thread-state persistence, inherit-on-omit behavior, and explicit reset to `default`.
- Real mailbox smoke on 2026-03-16 also verified the first-mail `[SYNC]` path by email: one request produced one `[SYNC] Project Folder List` reply with configured root listings, no task state capsule, and no runnable thread/session creation.
- Real mailbox smoke on 2026-03-18 fixed `[QUESTION] -> ANSWER -> DONE` as a repeatable acceptance path via `scripts/live_smoke_mail_question_answer.py`, with successful evidence in `E:\projects\mail_based_task_manager\_tmp_live_mail_question_smoke\opencode-question-20260318_133540-07ef74`.
- Real mailbox smoke on 2026-03-18 also fixed real-backend `KILL` in the normal mailbox loop via `scripts/live_smoke_mail_kill.py`, with successful evidence in `E:\projects\mail_based_task_manager\_tmp_live_mail_kill_smoke\codex-kill-20260318_133818-b4d2a5`.
- Real CLI / SDK runs on 2026-03-18 now parse structured run-result capsules into `RunResult.changed_files`, `tests_passed`, `error_type`, and `error_message`, with regression coverage in `tests/test_run_result_capsule.py`, `tests/test_cli_adapters.py`, `tests/test_codex_sdk_adapter.py`, and `tests/test_reporter.py`.
- Real mailbox smoke on 2026-03-18 also verified a live OpenCode path can project explicit `changed_files` and `tests_passed` into persisted `result.json` while stripping the machine-readable capsule from the user-visible `[DONE]` mail, with successful evidence in `E:\projects\mail_based_task_manager\_tmp_live_mail_structured_result_smoke\p7b-structured-20260318_141408-44bfca`.
- Mail ingress now scans `INBOX` by IMAP UID and persists a local processed-mail index under `task_root/_mailbox/processed_messages.json`; reading mail in the user mailbox no longer affects bot mailbox consumption.
- Bot-mailbox receive now supports best-effort IMAP `IDLE` when the server advertises it; unsupported or unstable servers automatically fall back to the existing UID-based polling path, so delivery detection gets faster without changing the truth layer.
- When COS delivery is configured, oversized outgoing artifacts stay in the `Artifacts` view but are no longer sent as MIME attachments; the status mail adds a separate `External Deliveries` section with presigned COS links instead.
- COS upload now uses a direct HTTPS session and does not inherit ambient `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` environment variables from the host process.

## Open Issues

- Non-reply mail still does not auto-route into an existing session by `workspace + title`; that remains a possible future refactor target.
- Cross-workspace session switching and non-reply reuse are still not implemented; explicit targeting is currently limited to same-workspace command routing.
- Single-mailbox self-replies remain provider-dependent and are no longer the recommended topology; current deployment guidance assumes separate user and bot mailboxes.
- Success/error summary extraction is heuristic and may need adjustment if future CLI output formats change substantially.
- Runtime outbound status mail still does not fully match the newly frozen consumer-facing contract; the current implementation track is to land P9 against that contract first and defer broader outbound convergence until after Android/Thunderbird validation.

## Phase 7 Summary

- Deliverables:
  - Native backend session id persistence plus resume paths for `codex exec resume` and `opencode run --session`
  - Failed-thread recovery fallback that replays replies as fresh runs when native resume is unavailable
  - Workspace/session indexes synced from `ThreadState`
  - Queue-aware background scheduler with a per-workspace concurrency cap plus a shared `max_active_sessions` cap for both active working-set size and cross-workspace concurrency
  - Follow-up queuing for a running session, plus restart recovery for queued and accepted work
  - Reply command surface including `/pause`, `/resume`, `/end`, `/new`, `/sessions`, `/status`, `/last`, `/continue`, `/rerun`, and `/kill`
  - Persisted `Permission:` control with reply-time inheritance and backend-specific highest-permission projection
  - First-mail `[SYNC]` project-folder sync for configured project roots, with one-level directory listing and unavailable-root reporting
- Validation:
  - `tests/test_app_phase2.py` covers first-mail `[SYNC]`, queued same-workspace sessions, and concurrent different-workspace sessions
  - `tests/test_app_phase3.py` covers reply resume, failed-thread recovery fallback, `/new`, `/kill`, `/end`, and risk-resume after kill
  - `tests/test_app_phase3.py` also covers auto-ending the oldest active session when a fifth active session is created or an ended thread is reactivated
  - `tests/test_app_phase6.py` covers `QUESTION -> ANSWER -> DONE` and waiting-state rerun rejection
  - `tests/test_app_phase7_pause.py` covers paused entry, paused plain-reply rejection, paused `/resume`, paused question restoration, and ended paused-thread reactivation
  - `tests/test_observe.py` covers active/ended lifecycle visibility plus health output in `status`, `list-running`, and `show-thread`
  - `tests/test_health_semantics.py` covers `normal`, `stale`, `suspected_stuck`, `orphaned`, and SDK stream-based progress updates
  - `tests/test_thread_store.py` covers persisted `lifecycle` / `last_active_at` / `last_progress_at` defaults and workspace thread enumeration
  - `tests/test_parser.py`, `tests/test_config.py`, `tests/test_intent_parser.py`, `tests/test_task_compiler.py`, and `tests/test_cli_adapters.py` cover `[SYNC]` subject parsing, project-root config loading, `Permission:` parsing, inheritance, and backend projection
  - `tests/test_task_compiler.py` covers native resume compilation plus failed-thread fallback compilation paths
  - `tests/test_runner.py` covers scheduler restart recovery, recovered callback replay, queued follow-ups, kill behavior, and `auto_create_workdir`
  - `tests/test_app_phase3.py` covers recovered accepted-task status-mail replay after restart
  - `tests/test_cli_adapters.py` covers native resume command generation and backend session id capture
  - `scripts/live_smoke_mail_permission.py` verified real-mailbox `Permission:` propagation for `Codex` and `OpenCode` in `_tmp_live_mail_permission_smoke\codex-permission-20260316_005939-b22854` and `_tmp_live_mail_permission_smoke\opencode-permission-20260316_011028-77bf33`
  - `scripts/live_smoke_mail_sync.py` verified the dual-mailbox real-mailbox first-mail `[SYNC]` roundtrip plus real inbox fetch path in `_tmp_live_mail_sync_smoke\sync-20260316_230059-cdf157`
  - `scripts/live_smoke_mail_question_answer.py` verified fixed real-mailbox `[QUESTION] -> ANSWER -> DONE` against a real backend in `_tmp_live_mail_question_smoke\opencode-question-20260318_133540-07ef74`
  - `scripts/live_smoke_mail_kill.py` verified fixed real-mailbox real-backend `KILL` in the normal mailbox loop in `_tmp_live_mail_kill_smoke\codex-kill-20260318_133818-b4d2a5`
  - `_tmp_live_mail_structured_result_smoke\p7b-structured-20260318_141408-44bfca` verified a live mailbox OpenCode run can emit a structured run-result capsule that lands as `changed_files=["docs/current/mail_protocol.md","README.md"]` and `tests_passed=false` while the outbound `[DONE]` mail still shows only the human-readable reply
  - `tests/test_run_result_capsule.py`, `tests/test_cli_adapters.py`, `tests/test_codex_sdk_adapter.py`, and `tests/test_reporter.py` verify structured run-result capsule parsing, field projection, and user-visible stripping

## History

### Phase 0

- Date: 2026-03-12
- Result: Completed
- Deliverables: package skeleton, core dataclasses, config loading, bootstrap app, prompt templates, baseline tests, and root-level project documentation.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 10 passed; `.\.venv\Scripts\python.exe -m mail_runner.app` -> bootstrap completed.

### Phase 1

- Date: 2026-03-12
- Result: Completed
- Deliverables: workspace persistence, thread state store skeleton, state capsule base implementation, mock adapter, dispatcher, local runner, and Phase 1 tests.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 21 passed; `.\.venv\Scripts\python.exe -m mail_runner.runner --snapshot <temp-seed> --task-root <temp-dir>` -> demo run completed successfully.

### Phase 2

- Date: 2026-03-12
- Result: Completed
- Deliverables: IMAP/SMTP SSL client, initial task parser, status reporter, `app --once` processing flow, and Phase 2 local integration tests.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 32 passed.
- Real Mailbox Validation: `.\.venv\Scripts\python.exe -m mail_runner.app --once --config .\mail_config.local.yaml` -> fetched=1, processed=1, skipped=0, failed=0; generated `thread_state.json` with `status=done` and `result.json` with `status=success`.

### Phase 3

- Date: 2026-03-12
- Result: Completed
- Deliverables: reply quote extraction, context assembly, rule-based intent parsing, task snapshot compilation, reply-thread matching, background mock kill, and reply-driven session continuation primitives.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 45 passed.
- Real Mailbox Validation: verified `NEW_TASK -> STATUS_QUERY -> KILL` against the real mailbox, with final `thread_state.status == "killed"` and `result.json.status == "killed"` in `E:\projects\mail_based_task_manager\_tmp_phase3_real_kill_ascii\tasks\thread_001`.

### Phase 4

- Date: 2026-03-12
- Result: Completed
- Deliverables: real `OpenCodeAdapter` / `CodexAdapter` thin wrappers, shared subprocess helper, prompt/log/result capture, runtime kill, and demo-mode validation.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 50 passed.
- Real CLI Verification: verified real `opencode` in `E:\projects\mail_based_task_manager\_tmp_phase4_real_op` and real `codex` in `E:\projects\mail_based_task_manager\_tmp_phase4_real_cx`, both with `result.json.status == "success"`.

### Phase 5

- Date: 2026-03-12
- Result: Completed
- Deliverables: success/error summary extraction for real adapters, improved status mail content, troubleshooting docs, and real mailbox + real backend end-to-end validation.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 51 passed.
- Real Mailbox Validation: verified `_tmp_phase5_mail\tasks\thread_001` for `[OC]` new task, `STATUS_QUERY`, and `RERUN`, and `_tmp_phase5_mail\tasks\thread_002` for `[CX]` new task.

### Phase 6

- Date: 2026-03-12
- Result: Completed
- Deliverables: explicit `question capsule` protocol, `awaiting_user_input` thread/run states, answer-driven snapshot regeneration, `[QUESTION]` mail rendering, optional `Profile:` parsing for new tasks and replies, and backend-specific `profile -> model` mapping from config.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 67 passed.
- Local Integration Validation: verified automated `QUESTION -> ANSWER -> DONE` flow and waiting-state `RERUN` rejection in `tests/test_app_phase6.py`.

### Phase 7

- Date: 2026-03-14
- Result: First-round delivery aligned in code and docs
- Deliverables: workspace/session scheduler metadata, queue-aware background execution, cross-workspace concurrency, runner restart recovery, `/sessions`, native backend resume, and updated current-state documentation.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 132 passed.

## Next Focus

- Keep the outbound authority stack aligned across `docs/current/pc_mail_output_protocol.md`, `docs/current/multimedia_mail_protocol.md`, `docs/plans/p9_html_mail_projection_plan.md`, and `docs/plans/android_consumer_protocol_freeze_note.md`.
- Land the narrow P9 HTML-reading slice against that frozen consumer contract before starting broader outbound convergence work.
- Keep neutral outbound model changes, summary-first plain-text convergence, and subject-shape cutover staged under `docs/plans/outbound_mail_contract_convergence_plan.md`, not under P9.
- Decide whether non-reply mail should eventually reuse an existing session by `workspace + title`.
- Decide whether session targeting should remain limited to same-workspace explicit commands or expand into cross-workspace routing and broader Android UX shortcuts.

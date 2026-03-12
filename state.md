# Project State

## Current Snapshot

- Updated At: 2026-03-12
- Current Phase: Phase 6
- Status: Completed
- Bootstrap Entry: `.\.venv\Scripts\python.exe -m mail_runner.app --once --config <mail_config.local.yaml>`
- Test Command: `.\.venv\Scripts\python.exe -m pytest`
- Notes: Phase 6 explicit question protocol, `awaiting_user_input` state handling, answer-driven resume flow, and backend-specific `profile -> model` mappings are now implemented. Local tests are green, and repo hygiene rules for local secrets, runtime state, and `_tmp_*` validation directories remain recorded in `.gitignore`.

## Completed Phases

- Phase 0 completed on 2026-03-12.
- Phase 1 completed on 2026-03-12.
- Phase 2 completed on 2026-03-12.
- Phase 3 completed on 2026-03-12.
- Phase 4 completed on 2026-03-12.
- Phase 5 completed on 2026-03-12.
- Phase 6 completed on 2026-03-12.

## Open Issues

- Real mailbox + real backend happy path is now validated for `[OC]`, `[CX]`, `STATUS_QUERY`, and `RERUN`, but `KILL` is still not part of the fixed real-backend mailbox acceptance path.
- `awaiting_user_input` and backend-specific profile mapping are now implemented, but `paused` still remains a reserved state without a user-facing protocol.
- Same-account SMTP self-replies may not reliably re-enter `INBOX` on all providers; deterministic reply validation used real IMAP inbox injection to avoid provider-specific routing behavior.
- Success/error summary extraction is heuristic and may need adjustment if future CLI output formats change substantially.
- Real mailbox + real backend `[QUESTION] -> ANSWER -> DONE` has not yet been added to the fixed acceptance path.

## Locked Notes

- The new requirements raised during Phase 1 are recorded for future phases and do not change the completed Phase 0 deliverable.
- Phase 1 intentionally stays focused on local state, workspace persistence, `thread_store` local skeleton, `state_capsule` base implementation, `MockAdapter`, `dispatcher`, and a local single-task happy path.
- Future Phase 2 / Phase 3 work may add reply-thread parsing, quote extraction, state-capsule recovery from replies, natural-language action parsing, and question/answer wait states such as `awaiting_user_input`.
- Phase 1 explicitly does not implement reply understanding, automatic Q&A turns, task suspend/resume, backend auto-upgrade, complex thread recovery, multi-task parallelism, or conversational mail collaboration.
- Status values are being centralized to reduce future refactoring, and `runner` / `dispatcher` are intentionally kept thin so later adapters can extend behavior without rewriting the Phase 1 core flow.
- `thread_store` currently remains a local persistence skeleton; message-id mapping and richer reply recovery are deferred to later phases.
- Phase 3 keeps reply handling minimal: header matching first, state capsule second, subject fallback last; no queueing or multi-worker scheduling is introduced.
- `profile` is now persisted in `TaskSnapshot`, `ThreadState`, and `ParsedMailAction`, and real adapters map it through backend-specific config tables. Raw model ids still do not belong in mail protocol fields.
- Phase 4 keeps real adapter integration thin: command discovery, prompt/log/result capture, and runtime kill are in scope; profile-driven model routing and structured CLI output parsing remain deferred.
- Phase 5 intentionally does not implement `[QUESTION]`, `awaiting_user_input`, or paused/resume behavior; it only stabilizes summaries, errors, docs, and the current real-mailbox happy path.
- Phase 6 implements explicit `question capsule` handling and `awaiting_user_input`, but recovery is still application-level snapshot regeneration, not native CLI session continuation.
- Local-only files and runtime artifacts are now explicitly isolated by `.gitignore`: `config.yaml`, `mail_config.local.yaml`, `tasks/*` except `.gitkeep`, and `_tmp_*/`.

## Next Phase

- Phase 7: native CLI session continuation experiments, `paused` protocol, and fixed real-mailbox acceptance for `[QUESTION]` and real-backend `KILL`.

## Planned Refactor Track

- Updated At: 2026-03-12
- Baseline is now frozen in git before the next architectural change.
- The next implementation track will pivot from a `thread`-centric model to a `workspace + session` scheduler model.
- The planned rollout is split into 3 gated phases:
  - Phase 1: workspace/session models and routing
  - Phase 2: queue-aware scheduler with workspace-level exclusivity
  - Phase 3: configurable multi-running sessions across different workspaces
- Each phase requires new tests plus a full regression pass before continuing.
- Detailed planning is recorded in `docs/session_scheduler_plan.md`.

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
- Manual Verification: verified generated `thread_state.json`, snapshot JSON, `result.json`, and `summary.md` in a temporary task root; final thread status was `done`.

### Phase 2

- Date: 2026-03-12
- Result: Completed
- Deliverables: IMAP/SMTP SSL client, initial task parser, status reporter, `app --once` processing flow, and Phase 2 local integration tests.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 32 passed.
- Real Mailbox Validation: `.\.venv\Scripts\python.exe -m mail_runner.app --once --config .\mail_config.local.yaml` -> fetched=1, processed=1, skipped=0, failed=0; generated `thread_state.json` with `status=done` and `result.json` with `status=success`.

### Phase 3

- Date: 2026-03-12
- Result: Completed
- Deliverables: reply quote extraction, context assembly, rule-based intent parsing, task snapshot compilation, reply-thread matching, background single-worker mock kill, and Phase 3 local integration tests.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 45 passed.
- Real Mailbox Validation: verified `NEW_TASK -> STATUS_QUERY -> KILL` against the real mailbox, with final `thread_state.status == "killed"` and `result.json.status == "killed"` in `E:\projects\mail_based_task_manager\_tmp_phase3_real_kill_ascii\tasks\thread_001`.
- Notes: `profile` is now persisted in `thread_state.json` and snapshot JSON as a reserved backend capability tier, but the current dispatcher and adapters still ignore it.

### Phase 4

- Date: 2026-03-12
- Result: Completed
- Deliverables: real `OpenCodeAdapter` / `CodexAdapter` thin wrappers, shared subprocess helper, prompt/log/result capture, runtime kill, default dispatcher switch to real adapters, and local demo-mode validation.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 50 passed.
- Manual Verification: `.\.venv\Scripts\python.exe -m mail_runner.runner --snapshot <seed> --config <demo-config>` succeeded for both demo `opencode` and demo `codex`, producing `prompt.txt`, `stdout.log`, `stderr.log`, `summary.md`, and `result.json` under `_tmp_phase4_demo_op` and `_tmp_phase4_demo_cx`.
- Real CLI Verification: `.\.venv\Scripts\python.exe -m mail_runner.runner --snapshot <seed> --config <config>` succeeded for real `opencode` in `E:\projects\mail_based_task_manager\_tmp_phase4_real_op` and real `codex` in `E:\projects\mail_based_task_manager\_tmp_phase4_real_cx`, both with `result.json.status == "success"` and no file content changes in the temporary repos.
- Environment Check: verified Windows command discovery resolves `opencode.cmd` and `codex.cmd` from `C:\Users\Administrator\AppData\Roaming\npm`.

### Phase 5

- Date: 2026-03-12
- Result: Completed
- Deliverables: success/error summary extraction for real adapters, status mail content improvements, troubleshooting and usage docs, and real mailbox + real backend end-to-end validation.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 51 passed.
- Real Runner Validation: verified real `opencode` in `E:\projects\mail_based_task_manager\_tmp_phase5_real_op` and real `codex` in `E:\projects\mail_based_task_manager\_tmp_phase5_real_cx`, with `thread_state.last_summary` sourced from real backend output.
- Real Mailbox Validation: verified `_tmp_phase5_mail\tasks\thread_001` for `[OC]` new task, `STATUS_QUERY`, and `RERUN`, and `_tmp_phase5_mail\tasks\thread_002` for `[CX]` new task; all completed with `result.json.status == "success"` and outgoing state mails recorded under each thread `mail/` directory.
- Live Mailbox Re-Validation: verified `_tmp_phase5_mail_live_ok\tasks\thread_001` for a fresh live `[OC]` new task plus `STATUS_QUERY`, and `_tmp_phase5_mail_live_cx\tasks\thread_001` for a fresh live `[CX]` new task; both completed with `result.json.status == "success"` and real backend summaries written into `thread_state.last_summary`.
- Repo Hygiene: added `.gitignore` to keep local secrets, runtime task state, virtualenv files, pytest cache, and `_tmp_*` validation outputs out of version control by default.

### Phase 6

- Date: 2026-03-12
- Result: Completed
- Deliverables: explicit `question capsule` protocol, `awaiting_user_input` thread/run states, answer-driven snapshot regeneration, `[QUESTION]` mail rendering, optional `Profile:` parsing for new tasks and replies, and backend-specific `profile -> model` mapping from config.
- Validation: `.\.venv\Scripts\python.exe -m pytest` -> 67 passed.
- Local Integration Validation: verified automated `QUESTION -> ANSWER -> DONE` flow and waiting-state `RERUN` rejection in `tests/test_app_phase6.py`.
- Notes: Phase 6 intentionally keeps recovery at the application layer; it does not yet use native `codex resume` / `opencode --continue` session continuation.

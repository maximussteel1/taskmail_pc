# AGENTS

## Scope

This file defines repository-specific guidance for coding agents working in
`mail_based_task_manager`. Keep it aligned with the current codebase, not with
future platform ideas.

## Environment

- Repository root: `E:\projects\mail_based_task_manager`
- Shell: PowerShell on Windows
- Preferred Python: `.venv\Scripts\python.exe`
- Do not rely on bare `python`; on this machine it may resolve to the Windows
  Store stub.

## Primary Commands

- Run the full test suite:
  - `.venv\Scripts\python.exe -m pytest`
- Run a targeted test module:
  - `.venv\Scripts\python.exe -m pytest tests/test_reporter.py`
- Run the mail runner once:
  - `.venv\Scripts\python.exe -m mail_runner.app --once --config .\mail_config.local.yaml`
- Run the local loop:
  - `.venv\Scripts\python.exe -m mail_runner.app --loop --config .\mail_config.local.yaml`

## Repository Layout

- `mail_runner/`: runtime code
- `tests/`: automated coverage; extend tests for behavior changes
- `docs/current/`: source of truth for current protocol and runtime behavior
- `docs/plans/`: repository-scoped implementation plans
- `docs/platform/`: future platform-facing docs, not current behavior
- `tasks/`: runtime state and artifacts; do not treat as committed source
- `_tmp_*/`: local verification output; keep if useful, otherwise ignore

## Documentation Rules

- If current behavior changes, update `docs/current/` first.
- If implementation direction changes but behavior does not, update
  `docs/plans/`.
- If `README.md`, `state.md`, and `docs/current/` disagree, treat
  `docs/current/` as the current protocol source of truth.
- Write repository documentation in Chinese by default. Unless the task
  explicitly requires another language, new docs and updated doc content should
  use Chinese.
- Keep documentation layered. Do not mix current runtime facts with speculative
  platform design in the same file.

## Mail And Artifact Boundaries

- Preserve the explicit mail protocol and state/question capsule behavior unless
  the task explicitly changes protocol semantics.
- Current run output delivery is local-workspace based.
- `RunArtifact` and `artifact_index.json` are the artifact truth layer.
- Canonical reporter authoring is Markdown-first, but outbound mail remains
  projected to `text/plain` + `text/html`.
- Keep a single `Artifacts` section in reporter output for now.
- Keep `Attachment Notices` as a separate section after `Artifacts`.
- Do not introduce mail-specific fields like `cid:` into
  `artifact_index.json`.

## Editing Expectations

- Prefer small, compatibility-preserving changes.
- Do not rewrite scheduler, reply-routing, or mail protocol behavior casually;
  these areas have tests and layered docs.
- When touching rendering, mail IO, parsing, or state persistence, add or
  update tests in the same change.
- Leave local config files such as `config.yaml` and `mail_config.local.yaml`
  uncommitted unless the task explicitly requires changing tracked examples.

## Validation

- For code changes, run targeted tests first, then the full suite if the change
  affects shared runtime paths.
- For documentation-only changes, tests are optional unless examples or command
  paths were altered.

## Service Maintenance

- For local Windows service maintenance, prefer the relay-enabled config
  `.\mail_config.bot.relay.local.yaml` with runtime dir
  `.\_tmp_live_mail_runner`.
- Treat `.\_tmp_live_mail_runner\host_state.json` as the first liveness truth
  for the hosted runner. Treat `.\_tmp_live_mail_runner\loop.pid` as a
  supporting hint, not a stronger truth source than `host_state.json`.
- If maintenance output disagrees, verify the host by checking the PID recorded
  in `host_state.json` and then reading recent `.\_tmp_live_mail_runner\loop.stderr.log`
  lines before assuming the runner is down.
- In agent or other non-interactive shells, `start` / `restart` may time out
  even after the detached host is already alive. After a timeout, check
  `host_state.json`, then run `scripts\manage_mail_runner.ps1 status`, before
  attempting another restart.
- Use `scripts\manage_mail_runner.ps1` for start/stop/restart/status rather
  than inventing ad-hoc launch commands. The script now prefers runtime
  metadata over CIM/WMI process scans and uses an external detached PowerShell
  launcher so service starts survive non-interactive shells used by coding
  agents.
- 对 relay-enabled 配置，`scripts\manage_mail_runner.ps1` 现在还会管理一个
  `sync_relay_task_root.py --repeat-seconds 2` companion，默认使用仓库根目录
  `work_bot.pem`，把本地 authoritative `task_root` 持续同步到 VPS relay 可见的
  `/opt/mail_runner_relay/shared/task_root`。
- 如果 `status` 显示 host 在跑但 `Relay task-root sync` companion 缺失，
  不要先回头怀疑 Android direct lane；先修复 companion 或 task-root 可见性，
  否则 `current-session` direct `reply` / `/status` 可能继续报 locator
  resolution failure。
- On this machine, do not switch the detached launcher back to
  `Register-ScheduledTask` for routine maintenance. In agent/elevated shells it
  can hang before the host starts. The maintained path is hidden
  `Start-Process powershell.exe ...` plus `host_state.json` verification.

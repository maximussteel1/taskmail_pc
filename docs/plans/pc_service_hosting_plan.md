# PC Service Hosting Plan

## Status

- Date: 2026-03-16
- Scope: concrete implementation plan for the `service hosting` workstream in `mail_based_task_manager`
- Related plan: `docs/plans/pc_background_hardening_plan.md`

## Problem

The current runner is still hosted like a development loop:

- `scripts/manage_mail_runner.ps1` starts a hidden Python process directly
- lifecycle management depends on ad hoc process discovery
- auto-start after machine reboot is not a first-class supported path
- abnormal exit recovery depends on manual intervention

This is workable for development and smoke testing, but it is not yet a robust supported hosting model for a long-running PC-side background process.

## Recommendation

### Recommended V1 Hosting Model

Use **Windows Task Scheduler + a dedicated Python host entrypoint** as the first supported production-style hosting path.

The intended stack is:

1. Windows Task Scheduler owns restart and auto-start policy
2. a new `mail_runner.host` entrypoint owns process-level runtime contract
3. the existing `run_forever()` loop remains the business loop

This is the recommended first implementation because it gives most of the operational value without forcing the repository to become a native Windows service project.

## Why This Approach

### Why Not Stay With the Current Direct `Start-Process` Model

The current script is good for development, but weak for supported operations:

- startup is manual
- crash recovery is manual
- single-instance semantics are process-scan based, not runtime-contract based
- health is inferred indirectly

### Why Not Build a Native Windows Service First

Using `pywin32` or a custom Service Control Manager integration is possible, but not the best first move:

- higher implementation complexity
- more Windows-specific lifecycle code
- harder debugging during early hardening
- poor cost/value ratio for a single long-running Python loop

### Why Not Adopt NSSM / WinSW First

Those are valid wrappers, but not the best first repository-native baseline:

- they add an extra external binary/runtime dependency
- they still benefit from an internal host contract for lock + heartbeat + state
- the repository can get most of the value using built-in Windows facilities first

They remain acceptable future options if a true service wrapper becomes desirable later.

## Target Architecture

### 1. Keep The Core Loop

Do not replace the current business loop.

Keep:

- `mail_runner.app.run_forever()`
- current config loading
- current scheduler / dispatcher behavior

The service-hosting slice should wrap the loop, not redesign it.

### 2. Add A New `mail_runner.host` Entry Point

Introduce a dedicated host module, for example:

- `python -m mail_runner.host --config ... --runtime-dir ...`

Responsibilities of the host layer:

- resolve runtime directory
- acquire a single-instance runtime lock
- write and update host-state metadata
- optionally emit heartbeat timestamps
- call the existing `run_forever()` loop
- catch top-level failures and classify exit status

Responsibilities that should stay outside the host layer:

- mail protocol parsing
- queue scheduling logic
- backend adapter semantics

### 3. Use Task Scheduler As The External Supervisor

Task Scheduler should own:

- start at boot
- optional delayed start after boot
- restart on abnormal exit
- “do not start a second instance” behavior at the task level

The scheduled command should point to the new host entrypoint, not directly to `mail_runner.app --loop`.

Example target shape:

```powershell
.\.venv\Scripts\python.exe -m mail_runner.host --config .\mail_config.bot.local.yaml --runtime-dir .\_tmp_live_mail_runner
```

## Runtime Contract

### Runtime Directory

Use the existing runtime directory as the canonical host-state location:

- `._tmp_live_mail_runner/`

Keep existing logs there and add host-specific state files beside them.

Suggested host files:

- `host.lock`
- `host_state.json`
- `host_heartbeat.json`
- `host_last_exit.json`

### Single-Instance Lock

Single-instance protection should move from “process scan only” to a real runtime lock.

Recommended contract:

- host acquires an exclusive lock for the process lifetime
- a second host instance exits quickly with a clear message and exit code
- lock release is automatic when the process dies

Preferred implementation direction:

- lock file in runtime dir held for the lifetime of the host process

This is better than only scanning Win32 processes because it gives a direct runtime ownership contract.

### Host State

`host_state.json` should expose at least:

- `status`: `starting | running | stopping | failed`
- `pid`
- `started_at`
- `updated_at`
- `config_path`
- `runtime_dir`
- `last_poll_completed_at` if available

This is not a full observability system.

It is only enough state to support hosting and operator status checks.

### Exit Semantics

The host should distinguish:

- normal operator stop
- duplicate-instance refusal
- startup/config failure
- loop crash / unhandled exception

Task Scheduler restart policy should only treat real failures as restart-worthy.

## Scheduled Task Design

### Registration Model

Support one canonical installed task, for example:

- `MailBasedTaskManagerRunner`

Recommended defaults:

- trigger: at system startup
- start delay: short optional delay, e.g. 30-60 seconds
- run whether user is logged on or not
- do not start a new instance if already running
- restart on failure

### Account Model

For V1, prefer running under the same Windows user that owns:

- the repository checkout
- `.venv`
- local config files
- local task/workspace artifacts

Do not optimize for `SYSTEM` or a custom service account first.

That can come later if deployment hardens further.

### Stop Semantics

Operator stop should be explicit and clean:

- stop the scheduled task
- wait for lock release
- preserve final host exit classification

Do not rely on killing arbitrary Python processes by pattern only once the scheduled-task path exists.

## Script Changes

### `manage_mail_runner.ps1`

Evolve the script from “direct process launcher” into “hosted runner controller”.

Recommended actions:

- `install`
- `uninstall`
- `start`
- `stop`
- `restart`
- `status`

Recommended behavior:

- `install` registers the scheduled task
- `start` starts the scheduled task
- `stop` stops the scheduled task
- `restart` stops then starts it
- `status` shows:
  - scheduled task state
  - host lock / host state
  - current PID if alive

Keep direct dev-mode loop execution documented separately, but do not treat it as the supported hosted mode.

## Implementation Phases

### Phase 1: Host Runtime Contract

Deliver:

- `mail_runner.host`
- runtime lock
- host state file
- clear exit codes

Do not change:

- Task Scheduler integration yet
- current business loop

### Phase 2: Scheduler Installation Path

Deliver:

- task registration script support
- start/stop/restart/status against scheduled task
- documented supported hosting command

Do not change:

- protocol behavior
- queue semantics

### Phase 3: Health Polish

Deliver:

- heartbeat timestamp
- startup failure capture
- clearer operator status rendering

Do not change:

- full observability scope
- interruption recovery logic

## Validation

Minimum validation for this slice:

1. install the scheduled task successfully
2. start the task successfully
3. verify the host lock blocks duplicate launch
4. verify `status` can show running vs stopped clearly
5. simulate abnormal process termination and verify scheduler restart
6. reboot or emulate boot-start validation at least once
7. verify operator stop produces a clean final state

## Explicit Non-Goals

This slice should not try to solve:

- full interruption recovery
- queue introspection beyond basic host state
- advanced observability dashboards
- security-boundary hardening
- multi-machine deployment

## Recommendation Summary

If the question is “what is the best first implementation for item 1?”, the answer is:

- do **not** start with a native Windows service
- do **not** stay on ad hoc hidden `Start-Process`
- do **start** with:
  - a repository-native `mail_runner.host` entrypoint
  - a runtime lock + host-state contract
  - Windows Task Scheduler as the external supervisor

That gives the best balance of reliability, implementation complexity, debuggability, and fit with the current repository.

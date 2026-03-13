# Task Mail Phase 0 Research

## 1. Goal Restatement

Target feature: add a dedicated Task Mail view layer on top of a Thunderbird for Android fork, without replacing normal mail flows.

Expected v1 scope:

- detect task threads
- expose a dedicated task entry
- show task-oriented thread list and thread detail UI
- parse `---TASK-STATE-BEGIN--- ... ---TASK-STATE-END---`
- render the human-readable task body as read-only Markdown
- keep all task-specific rendering isolated from normal mail views

## 2. Workspace Reality Check

Current repository is **not** a Thunderbird for Android fork.

Observed characteristics:

- language/runtime: Python
- build/test entry: `pytest`, `python -m mail_runner.app`
- core domain: mail-driven task runner and state persistence
- Android/Gradle markers not found:
  - `settings.gradle`
  - `build.gradle`
  - `AndroidManifest.xml`
  - `Activity` / `Fragment` / `Compose` / `RecyclerView`

Conclusion:

- this workspace cannot host the requested Android UI integration work yet
- Phase 0 can still identify reusable task-mail domain rules from the current codebase
- actual Phase 1+ implementation must happen in the real Thunderbird Android fork repository

## 3. Reusable Concepts Found In This Repository

The current Python project already contains task-mail concepts that map well to the requested Android feature.

### 3.1 Task detection signals

Relevant source:

- `mail_runner/parser.py`

Existing logic already recognizes:

- subject prefix `[OC]`
- subject prefix `[CX]`

This is a strong conceptual starting point for `TaskThreadDetector`.

### 3.2 Task state capsule markers

Relevant source:

- `mail_runner/state_capsule.py`

Existing logic already uses:

- `---TASK-STATE-BEGIN---`
- `---TASK-STATE-END---`

This is a direct match for the requested `TaskStateCapsuleParser`.

### 3.3 State-bearing task metadata

Relevant source:

- `mail_runner/models.py`

Current models already carry adjacent concepts:

- `thread_id`
- `task_id`
- `backend`
- `repo_path`
- `workdir`
- `mode`
- `status`
- `last_summary`

These fields overlap strongly with the proposed Android-side `TaskThreadSummary`, `TaskMessageDetail`, and `TaskStateCapsule`.

## 4. Gaps Between Current Repo And Requested Android Feature

The following required integration points do not exist in this workspace:

- Android module graph
- Thunderbird mail database access layer
- thread list UI entry point
- thread detail UI entry point
- Android-native Markdown rendering layer
- navigation shell for a task tab or drawer item

Because of this, the current repo can only inform the parsing rules and data model shape. It cannot provide the actual Thunderbird Android hook points.

## 5. Recommended Android Module Layout

When moved into the real Thunderbird Android fork, the feature should be introduced as a focused slice:

- `feature:taskmail:api`
- `feature:taskmail:internal`

### 5.1 `feature:taskmail:api`

Suggested responsibilities:

- expose `TaskThreadSummary`
- expose `TaskThreadDetail`
- expose `TaskMessageDetail`
- expose `TaskStateCapsule`
- expose task detail navigation contract if the host app uses feature-owned navigation

### 5.2 `feature:taskmail:internal`

Suggested responsibilities:

- `TaskThreadDetector`
- `TaskStateCapsuleParser`
- `TaskMessageExtractor`
- `TaskMarkdownRenderer`
- repository/use case layer for task-thread queries
- task thread list UI
- task thread detail UI

## 6. Recommended Data Flow In The Android Fork

### 6.1 Task list flow

1. query existing thread/message storage from Thunderbird Android
2. map thread candidates to detector inputs:
   - subject
   - latest plain-text body if available
   - sender/recipient if later needed
3. classify each thread with `TaskThreadDetector`
4. derive `TaskThreadSummary`
5. render only in task entry UI

### 6.2 Task detail flow

1. load thread messages from existing Thunderbird storage
2. for each message:
   - detect task message
   - extract capsule
   - parse capsule
   - remove capsule from body
   - decide `RenderMode`
3. render status section and human-readable body separately
4. keep fallback to plain text if Markdown conversion fails

## 7. Normal Mail vs Task Mail Split

Recommended split rule for the Android fork:

- task-only logic should run inside the dedicated task entry
- normal message list/detail screens should keep existing behavior
- optional future enhancement: add "Open in task view" from a normal message/thread screen

This preserves the host mail client behavior and matches the requested product boundary.

## 8. Proposed File/Module List For The Real Android Fork

These are the files I would expect to add once we are in the correct repository.

### 8.1 API module

- `feature/taskmail/api/src/main/java/.../TaskThreadSummary.kt`
- `feature/taskmail/api/src/main/java/.../TaskMessageDetail.kt`
- `feature/taskmail/api/src/main/java/.../TaskStateCapsule.kt`
- `feature/taskmail/api/src/main/java/.../TaskThreadDetail.kt`
- `feature/taskmail/api/src/main/java/.../RenderMode.kt`
- `feature/taskmail/api/src/main/java/.../TaskMailNavigator.kt`

### 8.2 Internal module

- `feature/taskmail/internal/src/main/java/.../domain/TaskThreadDetector.kt`
- `feature/taskmail/internal/src/main/java/.../domain/TaskStateCapsuleParser.kt`
- `feature/taskmail/internal/src/main/java/.../domain/TaskMessageExtractor.kt`
- `feature/taskmail/internal/src/main/java/.../render/TaskMarkdownRenderer.kt`
- `feature/taskmail/internal/src/main/java/.../data/TaskMailRepository.kt`
- `feature/taskmail/internal/src/main/java/.../data/TaskMailRepositoryImpl.kt`
- `feature/taskmail/internal/src/main/java/.../ui/list/TaskThreadListViewModel.kt`
- `feature/taskmail/internal/src/main/java/.../ui/list/TaskThreadListScreen.kt`
- `feature/taskmail/internal/src/main/java/.../ui/detail/TaskThreadDetailViewModel.kt`
- `feature/taskmail/internal/src/main/java/.../ui/detail/TaskThreadDetailScreen.kt`

## 9. Suggested Thunderbird Android Hook Points To Confirm In The Real Repo

Phase 0 in the real Android fork should inspect these concrete areas:

- app navigation host
- mailbox/folder entry points
- existing thread list screen and adapters/view models
- existing message/thread detail screen
- message body text extraction utilities
- dependency injection/module registration
- local mail/thread data query interfaces

## 10. Practical Recommendation

Do not start Phase 1 implementation in this repository.

Next required action:

- switch to the actual Thunderbird for Android fork workspace
- rerun Phase 0 there
- then implement the parsing/domain chain in Kotlin with local unit tests

## 11. Difficulty Assessment

Overall difficulty: **medium to high**.

Reasoning:

- parser and capsule logic are straightforward
- the real challenge is safe integration into Thunderbird Android's existing navigation, storage, and message rendering layers
- if the fork is modular and already has clean thread/message query abstractions, the task is closer to medium
- if the fork is heavily coupled or legacy-driven, the task becomes high

My estimate for the real Android repo:

- Phase 0-1: low to medium risk
- Phase 2-4: medium to high risk
- biggest risk: choosing the wrong UI/data hook and accidentally affecting normal mail rendering

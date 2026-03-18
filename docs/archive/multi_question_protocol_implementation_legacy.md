# Multi-Question Protocol Implementation Plan

> Archived legacy source file.
> Current canonical doc: `docs/current/multi_question_protocol.md`

## Status

- Date: 2026-03-14
- Purpose: upgrade the current single pending-question flow into a stable multi-question mail protocol
- Scope: protocol, parsing, persistence, resume input shaping, and tests

## Background

The current mail runner supports only one effective pending question at a time.

Observed behavior in the current implementation:

- the backend may emit multiple `---TASK-QUESTION-BEGIN---` blocks
- only the last block is parsed into the waiting state
- reply parsing in waiting state treats any non-empty reply as one free-form answer
- resume input passes the raw reply text through to the backend instead of a canonical answer summary

This is acceptable for one question, but unstable for a question set with multiple required answers.

## Goals

- support one backend turn producing multiple questions in a single waiting state
- let users answer multiple questions in one reply using a small, deterministic text format
- support partial answers without losing prior validated answers
- resume with canonical machine-checked answers instead of raw mail text
- preserve single-question compatibility

## Non-Goals

- do not introduce general NLP-based answer extraction for multi-question replies
- do not support complex nested forms in email
- do not support rich attachment-based question answering
- do not require users to learn a large DSL

## Design Principles

1. Multi-question flows must be deterministic.
2. Human-readable mail must remain copy-paste friendly.
3. Stored state must be canonical and backend-independent.
4. Resume input should be normalized, not raw.
5. Single-question flows should remain low-friction.

## Protocol Overview

### Backend Output

When the backend needs multiple answers, it emits one question set made of multiple question capsules.

Each capsule must contain the same `question_set_id`.

Example:

```text
---TASK-QUESTION-BEGIN---
question_set_id: phase2_clarifications
question_id: phase2_entry_position
question_type: single_choice
required: true
question_text: Where should the Tasks drawer entry be placed?
choices: top | below | section
choice_labels: top=Account list top | below=Account list bottom | section=Standalone section
---TASK-QUESTION-END---
---TASK-QUESTION-BEGIN---
question_set_id: phase2_clarifications
question_id: phase2_icon_strings
question_type: single_choice
required: true
question_text: Who provides icon and string resources?
choices: provide | reuse | placeholder
choice_labels: provide=You provide | reuse=Reuse existing | placeholder=Temporary placeholder
---TASK-QUESTION-END---
```

### User Reply

If there is more than one pending question, the user-facing reply format is:

```text
Answers:
phase2_entry_position: below
phase2_icon_strings: provide
phase2_k9_support: thunderbird_only
phase2_device_validation: acceptable
```

Allowed conveniences:

- `Answers:` heading is optional
- `:` and `：` are both accepted
- choice labels are accepted in addition to canonical keys
- blank lines are ignored
- parser only accepts `question_id` values that are still pending in the current waiting state
- if the same `question_id` appears multiple times, the last valid value wins

Also supported for real mailbox replies is the two-line style:

```text
question_id: phase2_entry_position
账户列表下方（设置附近）
question_id: phase2_icon_strings
你提供
question_id: phase2_k9_support
仅 Thunderbird
question_id: phase2_device_validation
可接受
```

Normalization rules:

- canonical keys are preferred for choice answers
- display labels from `choice_labels` are accepted and normalized into canonical keys
- normalization is deterministic; if a value does not map to one allowed choice, the answer is rejected
- parsing is line-oriented, not NLP-based

Not supported in multi-question mode:

- a free-form paragraph without `question_id: value` lines
- answers for unknown `question_id`
- values that cannot be normalized to one allowed choice
- relying on prose such as "same as above" or "pick the second one"

### Single-Question Compatibility

If exactly one pending question exists, the system still accepts:

- free text reply
- `question_id: value`
- `/resume` plus reply text
- optional `Profile: <name>` header before the answer body

Internally, single-question state will still be represented as a one-item question set.

## State Model

### New Dataclasses

Add these dataclasses to `mail_runner/models.py`:

```python
@dataclass(slots=True)
class QuestionItem:
    question_set_id: str
    question_id: str
    question_type: Literal["single_choice", "boolean", "short_text"]
    question_text: str
    required: bool = True
    choices: list[str] = field(default_factory=list)
    choice_labels: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class QuestionAnswer:
    question_id: str
    value: str
    raw_value: str
```

### ThreadState Changes

Add these fields to `ThreadState`:

- `pending_question_set_id: str | None = None`
- `pending_questions: list[QuestionItem] = field(default_factory=list)`
- `collected_answers: list[QuestionAnswer] = field(default_factory=list)`

Keep the old fields temporarily for compatibility:

- `pending_question_id`
- `pending_question_text`
- `pending_choices`

Compatibility rule:

- if old single-question fields exist and `pending_questions` is empty, synthesize one `QuestionItem`

### RunResult Changes

Add to `RunResult`:

- `question_set_id: str | None = None`
- `pending_questions: list[QuestionItem] = field(default_factory=list)`

Keep `question_id`, `question_text`, and `pending_choices` temporarily for legacy readers.

## Canonical Choice Rules

Choice questions must store canonical keys, not display text.

Normalization order:

1. exact match on canonical key
2. case-insensitive match on canonical key
3. exact match on label
4. whitespace-normalized match on label

Example mapping:

- `below` -> `账户列表下方（设置附近）`
- `provide` -> `你提供`
- `thunderbird_only` -> `仅 Thunderbird`
- `acceptable` -> `可接受`

The stored answer must always be the canonical key.

## Parsing Rules

### Question Capsule Parsing

New parser behavior in `mail_runner/state_capsule.py`:

- add `parse_question_capsules(text) -> list[dict[str, Any]]`
- parse all complete `TASK-QUESTION` blocks in order
- validate that all blocks in one mail share the same `question_set_id`
- if no `question_set_id` exists, treat it as a legacy one-question mail

Add `render_question_capsules(questions)` to render all pending questions.

`parse_question_capsule(text)` should remain as a thin wrapper:

- return the last item from `parse_question_capsules()` for legacy callers

### Reply Extraction

`mail_runner/quote_extractor.py` should keep the current quote trimming behavior, but tests must prove that it preserves:

- `Answers:` heading
- `question_id: value` lines
- Chinese reply bodies that include `---原始邮件---`

### Structured Answer Parsing

Add a parser in `mail_runner/intent_parser.py`:

```python
def parse_structured_answers(
    text: str,
    pending_questions: list[QuestionItem],
) -> StructuredAnswerParseResult:
    ...
```

Suggested result shape:

```python
@dataclass(slots=True)
class StructuredAnswerParseResult:
    answers: list[QuestionAnswer]
    unknown_question_ids: list[str]
    invalid_answers: list[str]
    missing_required_question_ids: list[str]
    used_structured_format: bool
```

Line parser rule:

- regex: `^\s*([A-Za-z0-9_.-]+)\s*[:：]\s*(.+?)\s*$`
- heading `Answers:` is ignored
- only keys in current `pending_questions` are accepted
- duplicate answers for the same `question_id` use the last valid value
- also support `question_id: <id>` followed by the next non-empty line as the answer value

### Intent Decision Rules

In waiting state:

- if `pending_questions` count is 1:
  - keep current free-text answer behavior
  - also accept structured answer lines
- if `pending_questions` count is 2 or more:
  - if structured answer lines are present, parse them
  - if no structured answer lines are present, do not continue; return a reply-needed status

Suggested new action or metadata approach:

- keep `ANSWER_QUESTION`
- attach parsed structured answers to the action
- add parser notes for invalid and missing answers

## Resume Input Design

### Problem

The current resume path uses `TaskSnapshot.turn_text = raw_user_text`, which is too lossy for multi-question replies and caused real runs to search for `phase2_entry_position`.

### New Rule

Resume input must be synthesized from validated answers, not copied from raw mail.

For multi-question answer completion, generate:

```text
Resolved answers for question set phase2_clarifications:
- phase2_entry_position: below
- phase2_icon_strings: provide
- phase2_k9_support: thunderbird_only
- phase2_device_validation: acceptable

Continue the task with these answers.
```

For partial answer state where required answers are still missing:

- do not resume
- re-send `[QUESTION]`

For single-question free text:

- preserve the current low-friction behavior
- optionally wrap it into a normalized format:

```text
Resolved answer for question q_001:
- q_001: <free text>

Continue the task with this answer.
```

## User-Facing Mail Format

### QUESTION Mail

`mail_runner/reporter.py` should render:

1. a short intro
2. question set id
3. received answers so far, if any
4. unanswered questions
5. allowed values for each choice question
6. a copy-paste `Answers:` template
7. state capsule
8. all question capsules
9. for choice questions, both canonical keys and labels when available

Example:

```text
The backend needs more information before it can continue.
Reply using this format:

Answers:
phase2_entry_position:
phase2_icon_strings:
phase2_k9_support:
phase2_device_validation:

Allowed values:
- phase2_entry_position: top | below | section
- phase2_icon_strings: provide | reuse | placeholder
- phase2_k9_support: both | thunderbird_only
- phase2_device_validation: acceptable | device_required
```

Recommended wording in the mail body:

- tell the user that multi-question replies should use the provided template
- mention that canonical keys are preferred
- mention that labels shown in the mail are also accepted
- mention that partial answers are allowed and will be saved

### Partial Answer Re-Prompt

If some answers are valid but some required answers are still missing:

- keep waiting state
- include `Received answers:` block
- list only remaining questions in the fresh template
- do not discard previously validated answers

### Invalid Answer Re-Prompt

If unknown ids or invalid values are present:

- keep waiting state
- keep all previously validated answers
- include clear error lines for each rejected item
- include a fresh template for unresolved questions
- include a short validation summary
- do not drop already valid answers

## Persistence Rules

### ThreadState Persistence

In `mail_runner/runner.py`:

- on `awaiting_user_input`, persist the full pending question set
- store validated collected answers separately
- clear both only when the run reaches a non-waiting terminal state

### Snapshot Persistence

When a reply completes the required question set:

- append a canonical answer summary to `task_text` only if needed for auditability
- store the canonical resume text in `turn_text`
- do not store raw user reply as the resume text in multi-question mode

### Compatibility Migration

When loading an old thread state:

- if `pending_questions` is empty but legacy fields are present:
  - create a one-item `pending_questions`
  - use `pending_question_id or question_<task_id>` as the synthetic id

## File-by-File Work Items

### `mail_runner/models.py`

- add `QuestionItem`
- add `QuestionAnswer`
- extend `ParsedMailAction` to carry structured answers or equivalent parsed result
- extend `ThreadState`
- extend `RunResult`
- add validation for new fields

### `mail_runner/state_capsule.py`

- add `parse_question_capsules`
- add `render_question_capsules`
- support `question_set_id`, `question_type`, `required`, and labels
- keep legacy wrapper for `parse_question_capsule`

### `mail_runner/context_layer.py`

- expose `pending_questions`
- expose `collected_answers`
- stop assuming one `pending_question`

### `mail_runner/intent_parser.py`

- add structured multi-answer parser
- normalize choice labels into canonical keys
- reject unstructured multi-question replies
- keep single-question free-text compatibility

### `mail_runner/task_compiler.py`

- generate canonical resume input
- stop using raw user text as `turn_text` in multi-question mode
- append structured answer summary to task context in a stable format

### `mail_runner/reporter.py`

- render multi-question mail body
- render partial answer summaries
- render answer template
- render all question capsules, not just one

### `mail_runner/runner.py`

- persist full question set and collected answers
- on partial answers, keep waiting state
- on complete answers, clear waiting state after successful resume dispatch

### `mail_runner/adapters/cli_common.py`

- collect multiple question capsules from stdout
- populate `RunResult.pending_questions`
- keep legacy fields populated for compatibility where useful

### `mail_runner/adapters/opencode_adapter.py`

- ensure resume command uses canonical summary text
- avoid blindly passing raw mail-derived multiline answer blocks as the direct semantic prompt

### `mail_runner/adapters/codex_adapter.py`

- same canonical resume summary behavior as OpenCode

### `mail_runner/quote_extractor.py`

- no large logic rewrite needed
- add tests for structured multi-answer reply bodies from real mail clients

## Validation and Error Rules

### Unknown Question IDs

If the reply includes:

```text
phase2_wrong_key: below
```

then:

- do not resume
- return `[QUESTION]`
- explain that `phase2_wrong_key` is unknown

### Invalid Choice Values

If the reply includes:

```text
phase2_entry_position: somewhere in the middle
```

then:

- do not resume
- return `[QUESTION]`
- list valid values for `phase2_entry_position`

### Missing Required Answers

If only 2 of 4 required questions are answered:

- keep the 2 validated answers
- do not resume
- re-prompt for the remaining 2

## Testing Plan

### Unit Tests

Add or extend tests in:

- `tests/test_state_capsule.py`
  - parse multiple question capsules
  - reject inconsistent `question_set_id`
  - round-trip render/parse for choice labels
- `tests/test_intent_parser.py`
  - parse `Answers:` block
  - accept `:` and `：`
  - normalize Chinese labels to canonical keys
  - reject unknown ids
  - reject invalid choice values
- `tests/test_task_compiler.py`
  - generate canonical resume summary
  - preserve partial answers without resuming

### Integration Tests

Add a new test module:

- `tests/test_app_phase6_multi_question.py`

Cover:

1. backend emits four questions in one waiting turn
2. thread state stores all four
3. user replies with two valid answers
4. app remains in `awaiting_user_input`
5. user replies with the remaining two
6. app resumes with canonical summary
7. final run completes

### Regression Fixture

Add a regression case based on the real `thread_020` flow:

- input mail contains the `question_id: ...` reply style from the real mailbox
- output must no longer pass raw answer text through to backend resume input

## Rollout Plan

### Step 1

Implement protocol parsing and persistence behind compatibility wrappers.

### Step 2

Switch reporter to emit multi-question templates.

### Step 3

Switch resume compilation to canonical answer summaries.

### Step 4

Run regression tests against the real reply fixture.

### Step 5

After the new path is stable, deprecate old single-question-only state fields.

## Acceptance Criteria

- a question set with 2+ questions is fully preserved in waiting state
- a structured reply is parsed deterministically
- partial answers are preserved and re-prompted
- resume input is canonical, not raw mail text
- single-question reply behavior still works
- the real `thread_020` scenario no longer misroutes answer lines into backend search behavior

## Recommended First Implementation Slice

To keep the first patch practical, implement only:

- `single_choice`
- `boolean`
- multi-question `Answers:` parsing
- canonical key normalization
- partial answer persistence
- canonical resume text generation

Defer until later:

- multi-line text answers
- optional question groups
- repeated question rounds in one mail

# PC Mail Output Protocol (Draft v0.1)

## 1. Purpose

This document freezes the minimum outbound mail contract for the PC-side mail runner so that:

- ordinary mail clients can carry and display the message,
- Android Task Detail can parse and project the mail into a timeline,
- Thunderbird for Android can use the HTML fragment as the primary reading surface while keeping plain-text fallback,
- future web preview can render the same HTML body fragment,
- protocol facts remain readable even when HTML rendering fails.

This is not a “pretty email” spec. It is a stable protocol-production spec.

---

## 2. Design Principles

1. Markdown may remain an internal authoring format, but it is not part of the receiver-facing outbound contract.
2. Outbound mail facts must not depend on Markdown parsing by the receiver.
3. Every task-related system mail must be readable in both `text/plain` and `text/html`.
4. Machine-readable facts must live in stable protocol blocks, not only in free-form prose.
5. HTML is a display mirror, not the only source of truth.
6. Outbound artifacts are file-based and manifest-driven.
7. Reply/session routing must always preserve standard mail headers.
8. Thunderbird for Android is the first mobile reading target for the HTML fragment contract.

---

## 3. Mail Categories

PC-side runtime sends four categories of mail.

### 3.1 New Task Mail

User → runtime, creates a fresh thread/session.

Required body fields:

- `Repo:`
- `Task:`

Optional body fields:

- `Workdir:`
- `Timeout:`
- `Mode:`
- `Profile:`
- `Permission:`
- `Acceptance:`

### 3.2 User Reply Mail

User → runtime, continues or controls an existing session.

Reply routing clues must be preserved in this order:

1. `In-Reply-To`
2. `References`
3. state capsule
4. `[S:<session_id>]` in subject

### 3.3 System Status Mail

Runtime → user, expresses progress, waiting state, or terminal receipt.

Visible tags are fixed:

- `[ACCEPTED]`
- `[RUNNING]`
- `[DONE]`
- `[FAILED]`
- `[KILLED]`
- `[PAUSED]`
- `[STATUS]`
- `[QUESTION]`
- `[SYNC]`

Semantics:

- progress: `[ACCEPTED]`, `[RUNNING]`, `[STATUS]`
- action required: `[QUESTION]`, `[PAUSED]`
- receipt: `[DONE]`, `[FAILED]`, `[KILLED]`
- control-only: `[SYNC]`

### 3.4 Sync Control Mail

`[SYNC]` mail does not belong to task-thread state projection.
It must not carry task state capsule or question capsules.

---

## 4. Required Headers

All task-related system mails must include:

- `Message-ID`
- `Subject`
- `X-Mail-Runner: 1`

All task-related reply mails must additionally include:

- `In-Reply-To`
- `References`

Recommended subject shape:

```text
[RUNNING] <display title> [S:<session_id>]
```

or

```text
[QUESTION] <display title> [S:<session_id>]
```

Notes:

- `[S:<session_id>]` is a weak fallback clue, not the primary routing key.
- subject normalization is display-only and must not replace session routing.

### 4.1 Transport Variants

The current repository may deliver the same outbound contract through either:

- direct `email` transport from the PC, or
- `relay` transport where the PC pushes one packet to the VPS and the VPS sends the user-facing mail.

Transport selection must not change:

- visible subject tags,
- plain-text / HTML body contract,
- attachment / inline-preview semantics,
- reply headers and session-routing behavior.

If `relay` is used for user-facing delivery, the relay acknowledgement must return the final delivered `Message-ID`
of the user-facing mail so the PC can preserve reply-chain continuity in local thread state.

---

## 5. MIME Structure

## 5.1 Minimum Structure

```text
multipart/mixed
├── multipart/alternative
│   ├── text/plain; charset=utf-8
│   └── text/html; charset=utf-8
└── normal attachments...
```

## 5.2 With Inline Images

```text
multipart/mixed
├── multipart/alternative
│   ├── text/plain; charset=utf-8
│   └── multipart/related
│       ├── text/html; charset=utf-8
│       └── inline image parts (Content-ID)
└── normal attachments...
```

Rules:

- always send `text/plain`,
- optionally send `text/html` mirror,
- inline image preview uses `cid:` references in HTML,
- original files remain normal attachments when attached,
- 内联预览 MIME part 不应携带附件文件名；避免同一源文件在客户端里显示成两份附件，
- externally delivered files are not attached and therefore not previewed inline.

---

## 6. Plain Text Body Contract

`text/plain` is required and is the protocol-safe fallback body.

Recommended ordering:

1. `Summary:`
2. `Permission:`
3. `Reply:` or waiting/resume hint
4. `Artifacts:` section
5. `External Deliveries:` section if present
6. `Attachment Notices:` section if present
7. structured run-result block if present
8. state capsule
9. question capsules if present

### 6.1 Example

```text
Summary: Implemented the task detail header and timeline cache wiring.
Permission: highest
Reply: Continue the session if further refinement is needed.

Artifacts:
- thunderbird_task_detail_dev_plan_aligned.md
- timeline_preview.png

---TASK-RUN-RESULT-BEGIN---
changed_files: feature/taskmail/presentation/detail/TaskDetailViewModel.kt|feature/taskmail/data/repository/TaskTimelineRepositoryImpl.kt
tests_passed: true
error_type:
error_message:
---TASK-RUN-RESULT-END---

---TASK-STATE-BEGIN---
thread_id: thread_020
workspace_id: ws_repo_workdir
session_id: s_20260318_001
session_name: task_detail_android
task_id: task_20260318_001
backend: codex
repo_path: E:\repo
workdir: E:\repo
mode: modify
status: done
last_summary: Header and timeline cache are now wired.
---TASK-STATE-END---
```

---

## 7. HTML Body Contract

`text/html` is a display mirror for mail clients, with Thunderbird for Android as the first mobile reading target,
and remains suitable for future web preview.

The consumer-facing HTML body contract is the `article.task-mail` fragment.

At SMTP transport time, runtime may still wrap that fragment in a minimal mail-safe `<html><body>` shell for
mail-client compatibility, but that outer wrapper is non-semantic and not part of the consumer contract.

Required semantic root:

```html
<article class="task-mail">
  ...
</article>
```

Recommended section ordering:

1. summary section
2. meta section
3. task body section
4. artifacts section
5. external deliveries section if present
6. attachment notices section if present
7. inline image previews section if present
8. run-result `<pre>`
9. state capsule `<pre>`
10. question capsules `<pre>`

### 7.1 Example

```html
<article class="task-mail">
  <section class="task-summary">
    <h3>Summary</h3>
    <p>Implemented the task detail header and timeline cache wiring.</p>
  </section>

  <section class="task-meta">
    <p><strong>Permission:</strong> highest</p>
    <p><strong>Reply:</strong> Continue the session if further refinement is needed.</p>
  </section>

  <section class="task-artifacts">
    <h4>Artifacts</h4>
    <ul>
      <li><a href="cid:artifact-1">thunderbird_task_detail_dev_plan_aligned.md</a></li>
      <li><a href="cid:artifact-2">timeline_preview.png</a></li>
    </ul>
  </section>

  <section class="task-attachment-notices">
    <h4>Attachment Notices</h4>
    <ul>
      <li>Skipped attachment because the file no longer exists.</li>
    </ul>
  </section>

  <section class="task-inline-previews">
    <h4>Inline Previews</h4>
    <p><img src="cid:artifact-2" alt="timeline preview" /></p>
  </section>

  <pre class="task-run-result">...</pre>
  <pre class="task-state-capsule">...</pre>
</article>
```

### 7.2 HTML Constraints

HTML must be:

- static,
- sanitized,
- self-contained,
- safe to render inside a controlled container.

Reading-priority note:

- optimize the fragment first for Thunderbird for Android narrow-screen reading,
- keep it ordinary-mail-client safe without relying on mail-client-specific hacks.

Consumer note:

- Android/mobile consumers should treat the `article.task-mail` subtree as the semantic body contract
- outer transport wrapper markup is not part of the consumer-facing body model
- if the fragment is missing or unsafe to project, clients may fall back to `text/plain`

Do not rely on:

- JavaScript,
- remote CSS frameworks,
- dynamic widgets,
- iframe/form/script execution,
- mail-client-specific rendering hacks.

---

## 8. Allowed HTML Subset

Allowed tags:

- `article`
- `section`
- `p`
- `br`
- `h1`–`h4`
- `ul`, `ol`, `li`
- `blockquote`
- `pre`, `code`
- `table`, `thead`, `tbody`, `tr`, `th`, `td`
- `a`
- `img`
- `strong`, `em`
- `hr`

Forbidden tags/features:

- `script`
- `iframe`
- `form`
- event attributes such as `onclick`
- arbitrary active content
- external JavaScript dependencies

Consumer degradation rule:

- clients may safely degrade unsupported allowed tags to plain text or explicit unsupported placeholders
- lack of full rendering fidelity for an allowed tag is not, by itself, a protocol failure

### 8.1 Link Rules

Allowed schemes:

- `https:`
- `http:`
- `mailto:`
- `cid:` in HTML generated for inline mail previews only

All external links should include:

```html
rel="noopener noreferrer"
```

Unknown or unsafe schemes must be downgraded to plain text.

---

## 9. Machine-Readable Blocks

## 9.1 State Capsule

All task-related system mails except `[SYNC]` must carry one state capsule.

Required markers:

- `---TASK-STATE-BEGIN---`
- `---TASK-STATE-END---`

Current canonical fields:

- `thread_id`
- `workspace_id`
- `session_id`
- `session_name`
- `task_id`
- `backend`
- `repo_path`
- `workdir`
- `mode`
- `status`
- `last_summary`

Rules:

- emit exactly one final state capsule block per mail,
- block lines use `key: value`,
- do not emit partial or malformed blocks,
- HTML may mirror the block, but plain text is canonical for parsing.

## 9.2 Question Capsules

Waiting-state mail must carry one or more question capsules.

Required markers:

- `---TASK-QUESTION-BEGIN---`
- `---TASK-QUESTION-END---`

Canonical fields per block:

- `question_set_id`
- `question_id`
- `question_type`
- `required`
- `question_text`
- `choices`
- `choice_labels`

Rules:

- one mail may contain multiple question blocks,
- all blocks in the same mail must share the same `question_set_id`,
- choice values use canonical keys,
- labels are presentation-only.

## 9.3 Run-Result Block

Terminal receipts should carry a structured run-result block.

Recommended markers:

- `---TASK-RUN-RESULT-BEGIN---`
- `---TASK-RUN-RESULT-END---`

Recommended fields:

- `changed_files`
- `tests_passed`
- `error_type`
- `error_message`

This block is parser-facing and should also be mirrored in HTML.

---

## 10. Waiting-State Mail Requirements

A waiting-state mail must include:

- visible tag `[QUESTION]` or `[PAUSED]`,
- state capsule,
- question capsules when awaiting answers,
- human-readable prompt,
- copy/paste-friendly reply template.

### 10.1 Multi-Question Reply Template

For multi-question waiting state, include:

```text
Answers:
phase2_entry_position:
phase2_icon_strings:
phase2_k9_support:
phase2_device_validation:
```

Also include allowed values and labels in the mail body.

### 10.2 Resume/Paused Hint

When paused, include an explicit hint that plain reply does not resume and `/resume` is required.

---

## 11. Attachment and Artifact Rules

## 11.1 Incoming Attachments

Incoming attachments are part of raw mail facts.

Runtime-side handling:

- materialize into active `workdir`,
- keep a raw archive copy,
- use deterministic prefixed filenames,
- do not overwrite by plain filename coincidence.

## 11.2 Outgoing Artifacts

Outgoing files are declared by manifest.

Primary protocol file:

```text
tasks/<thread_id>/runs/<task_id>/artifacts/manifest.json
```

### 11.2.1 Manifest Example

```json
{
  "version": 1,
  "items": [
    {
      "path": "E:\\repo\\src\\result_chart.png",
      "name": "result_chart.png",
      "mime": "image/png",
      "attach": true,
      "inline": true,
      "caption": "Result chart"
    },
    {
      "path": "E:\\repo\\src\\final_report.md",
      "name": "final_report.md",
      "mime": "text/markdown",
      "attach": true,
      "inline": false,
      "caption": "Final report"
    }
  ]
}
```

Rules:

- file paths may be arbitrary absolute paths,
- every outgoing file must be explicitly declared,
- mail layer must not infer attachments from stdout,
- if a remote relay transport is responsible for the final user-facing mail, the relay submission must include enough file materialization data for attached artifacts; a local filesystem path on the PC is not sufficient by itself,
- missing manifest items are skipped, not fatal,
- skipped files must be mentioned in the status body.

## 11.3 Oversized Delivery

For oversized artifacts:

- upload via external delivery,
- keep the item listed in `Artifacts`,
- add `External Deliveries` section,
- omit oversized file from MIME attachments,
- if upload fails, keep sending the status mail and include a failure notice.

Current runtime backend selection:

- if COS delivery is configured, oversized artifacts use COS
- otherwise, if `outbound_transport=relay` with `relay_url + relay_transport_token`,
  oversized artifacts use the relay host's `/v1/files` file surface
- relay file-surface external delivery writes a local
  `artifact_file_binding_index.json` sidecar alongside `artifact_index.json`

## 11.4 Inline Images

If `inline: true` and the file is attached in the current mail:

- include it as normal attachment,
- also reference it via `cid:` in HTML,
- keep preview and attachment logically tied to the same source file in MVP.

Matching rule note:

- consumers should match `cid:` image references to attachment `contentId`
- surrounding angle brackets on `Content-ID` may be normalized away
- filename, caption, or attachment-order heuristics are not part of the contract

Static SVG note:

- static attached `image/svg+xml` files may use the same inline-preview rule,
- this is the recommended path for formula-like rendered output intended for Android/mobile reading,
- inline-previewed SVG must be sanitized, self-contained, script-free, and external-resource-free,
- meaningful HTML `alt` text and plain-text readable fallback remain required.

If the file is externally delivered instead of attached:

- suppress inline preview for that mail.

---

## 12. TaskRunPacket Projection (Recommended)

In addition to the human-facing body, PC-side runtime should internally build a stable packet for downstream clients.

Recommended fields:

- `thread_id`
- `workspace_id`
- `session_id`
- `task_id`
- `backend`
- `repo_path`
- `workdir`
- `mode`
- `status`
- `lifecycle`
- `latest_summary`
- `pending_question_set`
- `run_result`
- `input_attachments`
- `output_artifacts`
- `body_text`
- `body_html`
- `raw_message_refs`

This packet does not need to be transmitted as a MIME part in MVP, but reporter should already produce all data needed to construct it.

---

## 13. Reporter Output Checklist

Every task-related system mail should be validated against this checklist.

### 13.1 Always Required

- [ ] Standard headers present
- [ ] `X-Mail-Runner: 1` present
- [ ] subject has valid visible tag
- [ ] `text/plain` present
- [ ] `text/html` present unless explicitly disabled
- [ ] state capsule present for all non-`[SYNC]` task system mail

### 13.2 Waiting-State Mail

- [ ] question capsules present
- [ ] reply template present
- [ ] canonical values present for choice questions
- [ ] paused/resume hints present when needed

### 13.3 Terminal Receipt Mail

- [ ] run-result block present
- [ ] artifacts summary present if any
- [ ] attachment notices present if relevant

### 13.4 Artifact Mail

- [ ] manifest resolved successfully
- [ ] MIME attachments match manifest attachable items
- [ ] inline previews only reference attached image parts

---

## 14. Non-Goals

This protocol does not attempt to provide:

- token-level streaming mail,
- title-based implicit session reuse,
- a second non-mail control protocol,
- client-side NLP interpretation of free text,
- protocol facts that exist only in HTML.

---

## 15. Implementation Order

Recommended implementation order:

1. freeze plain text template,
2. freeze HTML fragment shape and class names,
3. freeze state capsule fields,
4. freeze question capsule and waiting templates,
5. freeze run-result block,
6. freeze manifest and inline image rules,
7. refactor `reporter.py`,
8. wire `mail_io.py` MIME builder,
9. add parser/reporter integration tests,
10. run real mailbox smoke tests.

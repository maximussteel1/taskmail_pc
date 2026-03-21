# Android Runner Communication Contract

> Document layer: Layer 1 (current client integration contract)
>
> Current path: `docs/current/android_runner_communication_contract.md`

## Status

- Date: 2026-03-20
- Purpose: handoff document for the Android side that needs to communicate with the current `mail_based_task_manager`
- Scope: current Android send/read contract, current relay boundary, and implementation rules that remain valid whether the PC uses direct email or VPS relay

## 1. One-Line Contract

Android is still a **mail control-plane client**.

Even after the PC enables VPS relay, Android should continue to communicate with the system by:

- reading task/status mail from the `user mailbox`
- sending new mail or reply mail to the `bot mailbox`

Android should **not** implement the VPS relay WebSocket protocol as its primary app protocol in the current phase.

## 2. Current Topology

Current deployed topology:

```text
Android user
  -> user mailbox SMTP
  -> bot mailbox
  -> PC mail runner
  -> direct email OR relay transport
  -> user-facing mail
  -> user mailbox IMAP
  -> Android user
```

When relay is enabled, only the outbound PC -> user-facing delivery path changes:

```text
PC runner
  -> wss / relay packet
  -> VPS relay
  -> VPS SMTP
  -> user mailbox
  -> Android
```

What does not change:

- Android still reads mail from the `user mailbox`
- Android still sends control/reply mail to the `bot mailbox`
- reply routing still depends on normal mail headers plus the current capsule rules
- task execution truth still stays on the PC

## 3. What Android Must Implement

### 3.1 Read Path

Android should implement a normal task-mail consumer on top of the `user mailbox`.

Minimum requirements:

- read `multipart/alternative` mail containing `text/plain` and `text/html`
- prefer `text/html` for reading when present
- when HTML is present, treat `article.task-mail` as the semantic body root
- keep `text/plain` as the reply/parsing truth source
- preserve normal mail metadata:
  - `Message-ID`
  - `In-Reply-To`
  - `References`
  - `Subject`
  - `From`
  - `To`
  - `Date`
- parse current task-mail facts from the received mail:
  - visible status tag such as `[RUNNING]`, `[DONE]`, `[QUESTION]`
  - `[S:<session_id>]` when present in subject
  - structured run-result block
  - state capsule
  - question capsule set
  - attachment list and inline preview mapping

Android should treat the current outbound body contract as frozen by:

- `docs/current/pc_mail_output_protocol.md`
- `docs/current/multi_question_protocol.md`
- `docs/current/multimedia_mail_protocol.md`

### 3.2 Send Path

Android should send plain RFC-compatible mail, not a custom socket protocol.

Two current send modes exist.

#### A. New task mail

Use the current first-mail protocol:

- subject prefix: `[OC]` or `[CX]`
- required body fields:
  - `Repo:`
  - `Task:`
- optional body fields:
  - `Workdir:`
  - `Timeout:`
  - `Mode:`
  - `Profile:`
  - `Permission:`
  - `Acceptance:`

#### B. Continue or control an existing session

Android must send a real reply mail that preserves:

- `In-Reply-To`
- `References`
- original thread subject

If the subject already contains `[S:<session_id>]`, keep it unchanged.

For reply body serialization:

- `text/plain` is required
- `text/html` may exist as a compose mirror, but it is not required for correctness
- the first non-empty line must be the command if the action is command-based
- do not rely on HTML-only structure to carry machine meaning

Current command set Android should support emitting:

- `/pause`
- `/pause <session_id>`
- `/continue <session_id>`
- `/resume`
- `/resume <session_id>`
- `/end`
- `/end <session_id>`
- `/restart-runner`
- `/new`
- `/sessions`
- `/status`
- `/status <session_id>`
- `/last <session_id>`
- `/rerun`
- `/kill`
- `/kill <session_id>`

More precise reply rules remain defined by:

- `docs/current/android_reply_method_rules.md`
- `docs/current/mail_protocol.md`

### 3.3 Waiting-State Rules

Android must be state-aware when composing a reply.

If the thread is in `awaiting_user_input`:

- single-question flow may send natural-language answer text
- multi-question flow must send structured answers in the current capsule-defined format

If the thread is `paused`:

- Android must not send an implicit normal continuation
- the first non-empty line must be `/resume`
- if pending questions still exist, answers must follow the current resume rules after `/resume`

### 3.4 Attachments And Inline Previews

Android should implement the current mail attachment contract rather than invent relay-specific handling.

Minimum behavior:

- show normal attachments
- resolve inline previews through `cid:` and MIME `Content-ID`
- keep attachment handling compatible with the current `Artifacts` and `Attachment Notices` sections
- allow plain attachment-only reply when the user is sending supplemental files

## 4. What Relay Changes And What It Does Not Change

Relay changes the transport path, not the Android protocol.

### 4.1 What Changes

- the user-facing status mail may now be sent by VPS SMTP instead of directly by the PC
- the final delivered `Message-ID` is the VPS-delivered mail's `Message-ID`
- VPS now persists:
  - relay packet history
  - delivery attempts
  - relay session continuity

### 4.2 What Does Not Change

- status subject tags
- `text/plain` and `text/html` body contract
- state capsule and question capsule shapes
- attachment and inline preview semantics
- Android reply method
- current slash-command syntax
- task execution location

The correct Android behavior is therefore:

- treat relay-delivered mail exactly like direct-email-delivered mail
- reply to the received mail normally
- anchor continuity on the received `Message-ID`, `In-Reply-To`, and `References`

## 5. What Android Must Not Do

In the current phase, Android must not:

- connect to the relay `/relay` WebSocket directly as the main app protocol
- store or use the relay transport token
- treat `/healthz` or `/readyz` as app-facing business APIs
- log into the `bot mailbox` from the Android client
- infer session continuation only from title similarity
- rewrite or partially edit quoted `state capsule` / `question capsules`
- depend on relay-specific SMTP trace headers or `Received:` chain details

## 6. Current Boundary For Future VPS APIs

The current deployed VPS relay is a **PC transport endpoint**, not yet an Android application API.

Current deployed relay-facing endpoints are operator/transport endpoints only:

- `/relay`
- `/healthz`
- `/readyz`

That means:

- Android can rely on relay being present as infrastructure
- Android cannot yet rely on a separate current API for:
  - listing VPS-side sessions
  - reading VPS-side packet history
  - resuming work by talking directly to VPS instead of mail
  - direct Android-side send/receive over relay instead of mail

If Android later needs direct session/history APIs from VPS, that must be introduced as a new documented protocol, not inferred from the current relay deployment.

## 7. Recommended Android Module Split

To stay compatible with the current repository, Android should split implementation into five layers:

1. `mail ingress`
   - reads task mail from the `user mailbox`
2. `task mail parser`
   - parses status tags, capsules, attachments, and reply metadata
3. `local repository`
   - caches normalized thread/session message facts for incremental refresh
4. `reply composer`
   - serializes new task mail, reply mail, commands, and structured answers
5. `task UI`
   - renders HTML/plain-text projections and drives state-aware reply actions

Recommended local normalized fields:

- `messageId`
- `inReplyTo`
- `references`
- `subject`
- `statusTag`
- `sessionId`
- `threadId` when recoverable from capsule
- `taskId` when recoverable from capsule
- `plainBody`
- `htmlBody`
- `runResult`
- `stateCapsule`
- `questionCapsules`
- `attachments`
- `receivedAt`

## 8. Minimum Android Test Matrix

Before the Android side claims it can communicate with the current system, it should verify at least:

1. new task mail creation with `[OC]` and `[CX]`
2. normal reply continuation on a received status mail
3. targeted `/continue <session_id>` and `/status <session_id>`
4. single-question answer flow
5. multi-question structured answer flow
6. `/resume` flow from `paused`
7. `DONE` mail with attachments and inline previews
8. same user-visible behavior when PC uses:
   - direct `email`
   - `relay`

The last item is important: Android should observe that transport cutover does not require an Android protocol rewrite.

## 9. References

- `docs/current/mail_protocol.md`
- `docs/current/android_reply_method_rules.md`
- `docs/current/pc_mail_output_protocol.md`
- `docs/current/multi_question_protocol.md`
- `docs/current/multimedia_mail_protocol.md`
- `docs/plans/android_consumer_acceptance_requirements.md`
- `docs/platform/relay_transport_protocol_draft.md`

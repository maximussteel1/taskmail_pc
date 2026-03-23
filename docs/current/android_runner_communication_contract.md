# Android Runner Communication Contract

> Document layer: Layer 1 (current client integration contract)
>
> Current path: `docs/current/android_runner_communication_contract.md`

## Status

- Date: 2026-03-24
- Purpose: handoff document for the Android side that needs to communicate with the current `mail_based_task_manager`
- Scope: current Android send/read contract, current relay boundary, the optional direct `new_task` ingress, the optional bootstrap `[SYNC]` direct v1/v2 slices, the optional shared `/control` bootstrap v2 slice, the optional relay-side `/control transport_probe` harness slice, the optional current-session direct `/status` / plain `reply` slice, the optional Phase 3 v1 active-session-detail direct read sidecar, and current relay-hosted file-surface delivery notes

## 1. One-Line Contract

Android is still a **mail-first control-plane client**.

Even after the PC enables VPS relay, Android should continue to communicate with the system by:

- reading task/status mail from the `user mailbox`
- sending new mail or reply mail to the `bot mailbox`

Current direct exceptions:

- when the relay operator enables TaskMail direct ingress, Android may submit the first `new_task` action to `/relay`
- when the relay operator enables TaskMail direct ingress, Android may also submit the bootstrap `sync_project_folders` action to `/relay`
- when the relay operator provisions the current shared `/control` slice, Android may also submit bootstrap `sync_project_folders` to `/control`
- when the relay operator provisions the current shared `/control` harness slice, operator/debug builds may also submit relay-side `transport_probe` to `/control`
- bootstrap `sync_project_folders` currently has two direct variants:
- `taskmail-bootstrap-control-contract-v1` is bridge-to-mail; the relay bridges the accepted packet back into canonical `[SYNC]` mail ingress
- `taskmail-bootstrap-control-contract-v2` is direct-result; when the relay is provisioned with local PC truth, the server returns `packet_ack` followed by `bootstrap_result`
- on `/control`, the current first slice keeps `hello / hello_ack` and projects that same bootstrap v2 business action as `command -> command_ack -> result`
- `/control.hello_ack.accepted_payload_schemas` now depends on the provisioned relay handlers; bootstrap clients must still gate on `taskmail-bootstrap-control-contract-v2`
- current `/control transport_probe` is a harness/debug slice, not a normal end-user action; it currently returns `command_ack -> event* -> result(transport_probe_result)`
- current `transport_probe_result` now distinguishes four relay-native outcomes:
- `outcome=observed` means the relay submitted the deterministic probe mail and then read a matching PC mailbox observation from `tasks/_mailbox/transport_probes/<probe_id>.json`; this is the only `completed` outcome
- `outcome=timed_out` means the relay submitted the mail but did not observe matching PC mailbox evidence before `timeout_seconds`; the result status is `partial`
- `outcome=submitted` means the relay submitted the mail but could not perform PC observation lookup, typically because relay-visible `task_root` truth was not provisioned; the result status is `partial`
- `outcome=failed` means the relay failed before mail submission completed; the result status is `failed`
- `transport_probe_result.payload.observation` now carries either the projected PC mailbox observation summary or the current wait/skip state; Android should still treat the whole slice as operator/debug harness only
- when the relay operator enables the current post-creation slice, Android may submit current-session direct `/status` and current-session plain direct `reply` to `/relay`
- this post-creation slice is bridge-to-mail only; accepted packets still resolve to normal status or terminal mail on the canonical thread/session chain
- when the relay operator provisions the current Phase 3 direct inbound wire, Android may subscribe the current active session detail on `/relay`
- this Phase 3 path is read-side only and is limited to `session_snapshot` / `session_delta` freshness for one active session detail view
- oversized relay-hosted artifacts may now surface to Android as `/v1/files` download links inside normal task mail, but `/v1/files` is not a general Android control API

Android should **not** implement the VPS relay WebSocket protocol as a general-purpose or primary app protocol in the current phase.

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

Optional Phase 2 v1 direct-`new_task` ingress:

```text
Android user
  -> ws /relay hello
  -> ws /relay packet(new_task)
  -> VPS relay
  -> bot mailbox SMTP ingress
  -> PC mail runner
  -> direct email OR relay transport
  -> user mailbox
  -> Android user
```

Optional Phase 3 v1 direct active-session-detail sidecar:

```text
Android user
  -> ws /relay hello
  -> ws /relay packet(subscribe_session_detail)
  -> VPS relay
  -> current runtime session_state/thread_state projection
  -> ws /relay session_update(snapshot/delta)
  -> Android detail view
```

Optional bootstrap v2 direct `[SYNC]` roundtrip on a local-truth relay:

```text
Android user
  -> ws /relay hello
  -> ws /relay packet(sync_project_folders v2)
  -> local-truth relay runtime
  -> ws /relay packet_ack
  -> ws /relay bootstrap_result
  -> Android user
```

Optional shared `/control` bootstrap v2 roundtrip:

```text
Android user
  -> ws /control hello
  -> ws /control hello_ack
  -> ws /control command(sync_project_folders)
  -> ws /control command_ack
  -> ws /control result(sync_project_folders_result)
  -> Android user
```

Optional shared `/control` relay-side transport-probe harness:

```text
Android operator / harness
  -> ws /control hello
  -> ws /control hello_ack
  -> ws /control command(transport_probe)
  -> ws /control command_ack
  -> ws /control event*
  -> ws /control result(transport_probe_result)
```

Optional current-session direct `/status` or plain `reply`:

```text
Android user
  -> ws /relay hello
  -> ws /relay packet(status|reply)
  -> VPS relay
  -> bot mailbox SMTP ingress
  -> PC mail runner
  -> user-facing status/terminal mail
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
- Android 仍以 mail 作为默认 send path；只有显式 provision 的 direct surface 才走 `/relay`
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

Current optional direct-detail sidecar note:

- if the relay operator provisions the current Phase 3 direct inbound wire and Android is explicitly provisioned for it, Android may subscribe the currently opened active session detail on `/relay`
- this sidecar is only for detail freshness; mail remains the receipt, artifact, attachment, and history truth source
- Android must continue to keep the mail/local-cache read path as the fallback when the sidecar is unavailable, rejected, or gapped

### 3.2 Send Path

Android remains mail-first. The default send path is plain RFC-compatible mail; the only current direct-relay exceptions are the first-slice `new_task` ingress, the bootstrap `[SYNC]` direct v1/v2 slices, the shared `/control` bootstrap v2 slice, the current-session direct `/status` / plain `reply` slice, and the current active-session-detail read sidecar when the relay operator explicitly enables them.

Four current send modes exist.

#### A. New task creation

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

If the optional direct Phase 2 v1 ingress is used, Android must still compile to these same first-mail semantics. The relay currently accepts only the shared `phase2-direct-outbound-contract-v1` `new_task` packet shape and bridges it back into this existing first-mail path.

#### B. Continue or control an existing session

Android must send a real reply mail that preserves:

- `In-Reply-To`
- `References`
- original thread subject

If the subject already contains `[S:<session_id>]`, keep it unchanged.

#### C. Bootstrap project-folder sync

Use the current canonical `[SYNC]` first-mail semantics:

- subject: `[SYNC]`
- body may be empty
- no `In-Reply-To` / `References` required
- no task/thread/session is created

If the optional direct bootstrap ingress is used, Android must select the packet shape according to the provisioned relay capability:

- `taskmail-bootstrap-control-contract-v1`
- bridge-to-mail only
- `packet_ack.accepted=true` means the relay successfully injected canonical `[SYNC]` mail into the bot mailbox ingress
- it does not mean Android already has the final `[SYNC] Project Folder List`
- Android must continue reading the final result from the mailbox, not from a relay-native result frame

- `taskmail-bootstrap-control-contract-v2`
- only valid when the relay is provisioned with local PC truth for project-folder sync
- `packet_ack.accepted=true` means the direct result is already materialized or durably cached for replay
- Android must then wait for the direct `bootstrap_result`
- accepted v2 path does not create an additional `[SYNC] Project Folder List` mail
- after `accepted=true`, Android must reconnect/replay the same `packet_id` / `request_id` before considering any fallback

Current shared `/control` note:

- `/control` is on the same relay host/port as `/relay`
- `/control` and `/v1/files` reuse the same `Authorization: Bearer <transport_token>` path
- Android must still start with `hello` and wait for `hello_ack`
- Android should read `accepted_payload_schemas` from `hello_ack`; the current slice may advertise `taskmail-bootstrap-control-contract-v2` and/or `taskmail-transport-probe-payload-v1` depending on provisioned handlers
- current bootstrap `/control` business flow is `command(sync_project_folders) -> command_ack -> result(sync_project_folders_result)`
- current `transport_probe` `/control` business flow is `command(transport_probe) -> command_ack -> event* -> result(transport_probe_result)`
- current `transport_probe` is harness/debug only; Android must not treat it as a user-facing control primitive
- when `transport_probe_result.status=completed` and `outcome=observed`, the relay has already read a matching PC mailbox observation from `tasks/_mailbox/transport_probes/<probe_id>.json`
- when `status=partial`, Android should read `outcome=submitted|timed_out` plus `payload.observation` as operator diagnostics rather than as business success
- the PC host still writes the mailbox observation sidecar under `tasks/_mailbox/transport_probes/<probe_id>.json`; relay-side projection now reuses that same evidence instead of inventing a second proof surface
- after `command_ack.accepted=true`, reconnect/replay must reuse the same `packet_id` / `request_id`
- current `/control` does not replace `/relay` for `new_task`, current-session direct `/status` / `reply`, or Phase 3 detail subscribe

- for both v1 and v2, relay-level retry must reuse the same `packet_id` / `request_id`; only a fresh user tap creates a new pair

#### D. Current-session direct relay actions

When the relay operator enables the current post-creation slice, Android may submit a narrow current-session direct packet over `/relay`.

Current scope:

- schema: `post-creation-session-action-contract-v1`
- target scope: `current_session` only
- current supported actions:
  - `status`
  - `reply`

Current target identity:

- `workspace_id`
- `session_id`
- optional `thread_id`

Current `status` rules:

- semantic equivalent is canonical mail `/status`
- `task_run_packet.status` must be an empty object in `v1`
- accepted packet means the bridge mail was accepted; Android should still read the user-visible result from the normal status mail chain

Current plain `reply` rules:

- semantic equivalent is canonical plain continuation reply
- only plain natural-language continuation is supported in `v1`
- Android must not send:
  - slash-command reply text
  - structured question answers
  - attachments
- if the target session is `paused` or `awaiting_user_input`, Android should stay on the current mail path instead of trying to force direct plain `reply`

Current result reading:

- accepted packet is not the final business result
- current-session direct `/status` and plain `reply` still resolve to normal mail-visible status or terminal outcomes on the target thread/session chain

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
- the relay may now also accept a first-slice direct Android `new_task` packet and bridge it into the current bot-mailbox ingress path when explicitly configured
- the relay may now also accept a bootstrap direct Android `sync_project_folders` packet
- bootstrap `sync_project_folders` may run in bridge-to-mail v1 mode or in direct-result v2 mode when the relay has local PC truth available
- the relay may now also expose a narrow shared `/control` websocket slice for bootstrap `sync_project_folders v2` and relay-side `transport_probe`
- the relay may now also accept a narrow current-session direct `/status` and plain `reply` slice when explicitly provisioned
- the relay may now also accept a narrow Phase 3 direct active-session-detail subscribe path and push `session_update` messages when explicitly provisioned
- oversized outgoing artifacts may now be hosted on the relay file surface and appear to Android as relay-hosted external delivery URLs
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
- 绝大多数 reply/control action 在 task creation 后仍保持 mail-first；当前 direct 例外只限 current-session `/status` 与 plain `reply`

The correct Android behavior is therefore:

- treat relay-delivered mail exactly like direct-email-delivered mail
- reply to the received mail normally
- anchor continuity on the received `Message-ID`, `In-Reply-To`, and `References`

## 5. What Android Must Not Do

In the current phase, Android must not:

- connect to the relay `/relay` WebSocket directly as the main or general-purpose app protocol
- treat `/control` as a general-purpose or primary Android protocol
- store or use a general-purpose relay transport token outside the narrow, operator-provisioned TaskMail direct scopes
- treat `/healthz` or `/readyz` as app-facing business APIs
- log into the `bot mailbox` from the Android client
- infer session continuation only from title similarity
- rewrite or partially edit quoted `state capsule` / `question capsules`
- depend on relay-specific SMTP trace headers or `Received:` chain details
- use direct relay packets for `/pause`, `/resume`, `/end`, `/kill`, `/last`, `/sessions`, structured question answers, attachment-bearing post-creation actions, or any non-`current_session` direct target

## 6. Current Boundary For Future VPS APIs

The current deployed VPS relay is still **not a general Android application API**.

Current deployed relay-facing endpoints are operator/transport endpoints only:

- `/relay`
- `/control`
- `/v1/files`
- `/healthz`
- `/readyz`

Current Android-facing direct support is limited to several narrow slices plus one relay-hosted external-delivery surface:

- first-scope `new_task` over the shared `phase2-direct-outbound-contract-v1` packet shape
- only when the relay operator enables TaskMail direct ingress
- bridge-to-mail semantics on the server side
- bootstrap `sync_project_folders` over the shared `taskmail-bootstrap-control-contract-v1` or `taskmail-bootstrap-control-contract-v2` packet shape
- only when the relay operator enables TaskMail direct ingress
- v1 keeps bridge-to-mail semantics; final `[SYNC]` result is still mailbox-read
- v2 returns `packet_ack + bootstrap_result` directly when the relay has local PC truth; it still does not create task/thread/session
- shared `/control` bootstrap `sync_project_folders v2`
- only when the relay operator provisions the current `/control` slice
- current bootstrap flow is `hello -> hello_ack -> command -> command_ack -> result`
- relay-side `/control transport_probe`
- only for operator/debug harness use, not as a normal end-user action
- current flow is `hello -> hello_ack -> command -> command_ack -> event* -> result`
- current accepted payload set is runtime-dependent; bootstrap clients must still gate on `taskmail-bootstrap-control-contract-v2`, and probe clients must still gate on `taskmail-transport-probe-payload-v1`
- current-session direct `/status` and plain `reply` over `post-creation-session-action-contract-v1`
- only when the relay operator enables the current post-creation slice
- this slice is bridge-to-mail only; accepted packets still resolve to canonical status/terminal mail, not a new direct terminal-result API
- active-session-detail subscribe over the shared `phase3-direct-inbound-wire-v1` wire
- only when the relay operator provisions the current direct detail sidecar
- direct detail is read-side only; it is not a direct session history or control API
- relay-hosted oversized artifact delivery via `/v1/files`
- Android may consume those links as external delivery URLs, but `/v1/files` is not a general Android-side control or session API

That means:

- Android can rely on relay being present as infrastructure
- Android cannot yet rely on a separate current API for:
  - listing VPS-side sessions
  - reading VPS-side packet history
  - resuming work by talking directly to VPS instead of mail
  - direct Android-side send/receive over relay instead of mail, except for the narrow `new_task` ingress, the narrow bootstrap `[SYNC]` v1/v2 slice, the narrow shared `/control` bootstrap v2 slice, the narrow `/control transport_probe` harness slice, the narrow current-session direct `/status` / plain `reply` slice, and the narrow active-session-detail read sidecar above

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
9. optional Phase 2 v1 direct `new_task` ingress when the relay operator enables it
10. optional Phase 3 v1 direct active-session-detail subscribe, including `packet_ack -> session_snapshot` and mail/local-cache fallback on reject or gap

The last item is important: Android should observe that transport cutover does not require an Android protocol rewrite.

## 9. References

- `docs/current/mail_protocol.md`
- `docs/current/android_reply_method_rules.md`
- `docs/current/pc_mail_output_protocol.md`
- `docs/current/multi_question_protocol.md`
- `docs/current/multimedia_mail_protocol.md`
- `docs/plans/android_consumer_acceptance_requirements.md`
- `docs/platform/relay_transport_protocol_draft.md`

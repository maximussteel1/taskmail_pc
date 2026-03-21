# Task Manager Migration Tasklist

## 1. Goal of this phase

This phase does **not** replace email.

This phase only does two things:

1. Split PC and Android internals so business logic no longer depends directly on email transport.
2. Add a local `UnifiedMessage` cache on Android so refresh becomes incremental instead of full re-parse and full re-render.

Current user-facing behavior should remain compatible with the email workflow.

---

## 2. Scope freeze for this phase

### In scope

- Freeze minimal HTML boundary
- Introduce minimal `TaskRunPacket`
- Split PC into render / packet / transport
- Split Android into ingress / parser / repository / sync / UI
- Add Android local persistence for `UnifiedMessage`
- Keep email as the only formal production channel
- Prepare extension points for future relay transport

### Out of scope

- Full relay protocol implementation
- WebSocket
- Background push
- Encryption
- Multi-device sync strategy
- Message edit / recall
- Complex HTML render cache
- UI redesign

---

## 3. Architectural target

### 3.1 PC side target chain

`TaskRunner -> ResultNormalizer -> HtmlRenderer -> TaskRunPacketBuilder -> OutboundDispatcher -> EmailTransport`

Future extension:

`TaskRunner -> ResultNormalizer -> HtmlRenderer -> TaskRunPacketBuilder -> OutboundDispatcher -> EmailTransport | RelayTransport`

### 3.2 Android side target chain

`EmailIngress -> EmailMessageParser -> UnifiedMessageRepository -> MessageSyncCoordinator -> TaskDetailViewModel -> UI`

Future extension:

`EmailIngress | RelayIngress -> EmailMessageParser | RelayPacketParser -> UnifiedMessageRepository -> MessageSyncCoordinator -> TaskDetailViewModel -> UI`

---

## 4. Contracts to freeze now

## 4.1 Minimal HTML boundary

Freeze only the minimum:

- Standard presentation payload is `html`
- `text_fallback` remains available
- HTML is produced on PC side
- Android consumes HTML directly
- Transport layer must not rebuild message body
- UI layer must not reinterpret raw markdown as the main path

Do **not** try to finish the final HTML visual system in this phase.

## 4.2 Minimal TaskRunPacket

Suggested fields:

- `packet_id`
- `task_id`
- `created_at`
- `message_kind`
- `content_format` = `html`
- `html`
- `text_fallback`
- `parent_packet_id` (nullable)
- `state_patch` (nullable)
- `client_trace_id` (nullable)

### Must not appear in TaskRunPacket

- `imap_uid`
- `smtp_message_id`
- `mailbox_folder`
- MIME-specific internal fields
- transport retry counters

Packet is a **business envelope**, not a transport envelope.

---

## 5. PC-side refactor plan

## 5.1 Package split

Recommended structure:

```text
pc/
  contract/
    task_run_packet.py
    task_state_patch.py
    transport_receipt.py

  render/
    result_normalizer.py
    html_renderer.py
    html_envelope_builder.py

  packet/
    task_run_packet_builder.py
    packet_id_generator.py

  transport/
    outbound_transport.py
    outbound_dispatcher.py
    email_transport.py
    relay_transport.py   # stub only in this phase

  journal/
    outbound_journal.py
    delivery_attempt.py
```

## 5.2 Class responsibilities

### `TaskRunPacket`
- unified business payload
- no email-specific transport data

### `TaskStatePatch`
- optional compact state delta for task progress
- keeps task state separate from transport

### `TransportReceipt`
- result of a send attempt
- minimal fields: `success`, `transport_name`, `transport_message_id`, `sent_at`, `error_message`

### `ResultNormalizer`
- converts raw task/tool output into normalized content blocks
- prepares data for HTML rendering

### `HtmlRenderer`
- converts normalized content into final HTML
- owns HTML formatting logic for this phase

### `HtmlEnvelopeBuilder`
- wraps content into the minimal frozen HTML boundary
- central place for stable HTML shell changes

### `TaskRunPacketBuilder`
- builds `TaskRunPacket` from rendered HTML and metadata

### `PacketIdGenerator`
- generates stable packet IDs and trace IDs

### `OutboundTransport`
- interface for sending packets
- first phase method can be as simple as `send(packet) -> TransportReceipt`

### `EmailTransport`
- contains current email send implementation
- must only send packet content, not generate business HTML

### `RelayTransport`
- stub only
- no production logic in this phase

### `OutboundDispatcher`
- receives packet and delegates to configured transport
- first phase can route only to `EmailTransport`

### `OutboundJournal`
- records send attempts
- used later for transport comparison and debugging

### `DeliveryAttempt`
- one send attempt record
- recommended fields:
  - `packet_id`
  - `task_id`
  - `transport_name`
  - `started_at`
  - `finished_at`
  - `success`
  - `error_message`

## 5.3 PC migration steps

### Phase PC-1: create new skeleton only
- create `TaskRunPacket`
- create `TransportReceipt`
- create `OutboundTransport`
- create `OutboundDispatcher`
- create `HtmlRenderer` shell

### Phase PC-2: move old logic into new shells
- move current HTML generation into `HtmlRenderer`
- move current mail send logic into `EmailTransport`
- keep external behavior unchanged

### Phase PC-3: add packet build stage
- insert `TaskRunPacketBuilder` between render and transport
- ensure transport receives packet, not ad-hoc raw fields

### Phase PC-4: add send journal
- add `OutboundJournal`
- log every email send attempt

## 5.4 PC acceptance criteria

- Existing email sending still works
- Business layer no longer calls mail send code directly
- HTML generation is no longer embedded inside transport
- `TaskRunPacket` exists and is used on the main path
- Journal records are produced for each send

---

## 6. Android-side refactor plan

## 6.1 Package split

Recommended structure:

```text
android/.../data/
  ingress/
    MessageIngress.kt
    EmailIngress.kt
    RelayIngress.kt      // stub

  parser/
    IncomingMessageParser.kt
    EmailMessageParser.kt
    RelayPacketParser.kt // stub

  local/
    entity/
      UnifiedMessageEntity.kt
      MessageSyncStateEntity.kt
    dao/
      UnifiedMessageDao.kt
      MessageSyncStateDao.kt
    db/
      TaskMessageDatabase.kt

  repository/
    UnifiedMessageRepository.kt
    MessageSyncStateRepository.kt

  sync/
    MessageSyncCoordinator.kt

  ui/taskdetail/
    TaskDetailViewModel.kt
    TaskMessageUiMapper.kt
```

## 6.2 Class responsibilities

### `MessageIngress`
- source abstraction for fetching raw input messages
- first-phase methods:
  - `fetchLatest()`
  - `fetchSince(cursor)`
  - `startForegroundSync()` (optional placeholder)
  - `stopForegroundSync()` (optional placeholder)

### `EmailIngress`
- fetches raw email messages only
- must not output UI models directly

### `RelayIngress`
- stub only in this phase

### `IncomingMessageParser<Raw, UnifiedMessage>`
- parser abstraction from raw source payload to unified message

### `EmailMessageParser`
- converts raw email into `UnifiedMessage`
- extracts task-related fields
- derives packet ID when available
- computes content hash and sort key

### `RelayPacketParser`
- stub only in this phase

### `UnifiedMessageEntity`
- persisted unified message model
- central cached representation for UI and sync

Suggested fields:

- `localId`
- `packetId`
- `taskId`
- `source`
- `sourceMessageId`
- `direction`
- `html`
- `textFallback`
- `createdAt`
- `sortKey`
- `status`
- `contentHash`
- `parserVersion`
- `updatedAt`

### `MessageSyncStateEntity`
- persisted sync cursor state

Suggested fields:

- `source`
- `scopeKey`
- `lastCursor`
- `lastSyncAt`

### `UnifiedMessageDao`
Suggested methods:
- `observeByTaskId(taskId)`
- `findBySourceMessageId(source, sourceMessageId)`
- `insertAll(...)`
- `update(...)`
- `upsert(...)`

### `MessageSyncStateDao`
Suggested methods:
- `get(source, scopeKey)`
- `upsert(state)`

### `UnifiedMessageRepository`
Responsibilities:
- persist unified messages
- dedupe
- expose query streams to ViewModel
- manage message upsert rules

Suggested methods:
- `observeMessages(taskId)`
- `upsertMessages(messages)`
- `getExistingMessage(...)`

### `MessageSyncStateRepository`
Responsibilities:
- store and retrieve sync cursors

### `MessageSyncCoordinator`
Responsibilities:
- orchestrate sync flow
- read cursor
- call ingress
- call parser
- call repository
- update cursor
- keep cursor logic out of ViewModel

### `TaskDetailViewModel`
Responsibilities:
- observe unified messages only from repository
- trigger sync through coordinator
- must not parse emails and must not own cursor logic

### `TaskMessageUiMapper`
- maps persisted unified message entity to UI model
- UI remains transport-agnostic

---

## 7. Android local cache and incremental refresh

This is **part of this phase**, not a later optimization.

## 7.1 What to cache now

Cache **UnifiedMessage**, not Android view objects.

Good for this phase:
- local persistence of unified messages
- incremental upsert
- sync cursor persistence
- content hash comparison
- parser version tracking

Not for this phase:
- WebView instance cache
- DOM patch cache
- RecyclerView visual tree cache
- complex HTML render cache

## 7.2 Incremental refresh flow

Standard flow:

1. UI opens task detail
2. ViewModel first reads local cached `UnifiedMessage`
3. Existing local messages render immediately
4. `MessageSyncCoordinator` runs incremental sync
5. Ingress fetches raw messages since cursor
6. Parser converts raw messages into unified messages
7. Repository performs upsert
8. DAO emits changes
9. UI updates only changed items

## 7.3 Upsert rules

Recommended dedupe order:

1. `packetId` when stable and available
2. fallback: `source + sourceMessageId`

A message should be updated when:
- no existing record exists
- `contentHash` changed
- `status` changed
- `parserVersion` changed

## 7.4 Cursor strategy

For first phase, prefer a **global source cursor** rather than a task-specific cursor.

Recommended initial scope:
- `source=email`
- `scopeKey=global`

Reason:
- email behaves more like a global stream
- simpler initial logic
- task filtering can happen after parsing and local persistence

---

## 8. Dependency rules that must be enforced

## 8.1 PC side

Allowed direction only:

`runner -> render -> packet -> transport`

Forbidden patterns:
- transport building business HTML
- transport mutating packet business meaning
- packet containing email-only fields

## 8.2 Android side

Allowed direction only:

`ingress -> parser -> repository -> sync -> viewmodel -> ui`

Forbidden patterns:
- UI directly calling `EmailIngress`
- parser depending on UI models
- ViewModel owning cursor state
- repository branching UI behavior by source type

---

## 9. Cross-side compatibility rules

These rules preserve email compatibility while preparing future relay support.

### Rule 1
PC emits stable HTML through one renderer path.

### Rule 2
Transport only sends payload; it does not define business message shape.

### Rule 3
Android parser normalizes all source formats into one unified model.

### Rule 4
UI reads only unified local messages.

### Rule 5
Email remains a valid implementation on both sides:
- PC: `EmailTransport`
- Android: `EmailIngress + EmailMessageParser`

---

## 10. Recommended migration order

## Step 1: freeze minimal HTML boundary
- confirm standard payload is `html`
- confirm `text_fallback`
- stop expanding HTML scope for now

## Step 2: build PC-side skeleton
- add packet types
- add transport abstraction
- add renderer shell
- add dispatcher

## Step 3: move existing PC code
- move mail sending to `EmailTransport`
- move HTML generation to `HtmlRenderer`
- add packet build in the middle

## Step 4: build Android-side skeleton
- add `MessageIngress`
- add `IncomingMessageParser`
- add local entities and DAO
- add repository shells

## Step 5: move existing Android code
- move email fetch logic to `EmailIngress`
- move email parse logic to `EmailMessageParser`
- redirect Task Detail to repository-backed data

## Step 6: enable local cache path
- persist unified messages
- persist sync cursor
- read local first, then sync incrementally

## Step 7: stabilize email-compatible version
- verify current email workflow still works
- verify incremental refresh works
- verify no full re-parse is needed on every refresh

## Step 8: only after stabilization, start relay work
- add `RelayTransport` implementation
- add `RelayIngress`
- add `RelayPacketParser`
- run shadow mode later

---

## 11. Concrete task checklist

## 11.1 PC mandatory tasks

- [ ] Create `TaskRunPacket`
- [ ] Create `TaskStatePatch`
- [ ] Create `TransportReceipt`
- [ ] Create `ResultNormalizer`
- [ ] Create `HtmlRenderer`
- [ ] Create `HtmlEnvelopeBuilder`
- [ ] Create `TaskRunPacketBuilder`
- [ ] Create `PacketIdGenerator`
- [ ] Create `OutboundTransport`
- [ ] Create `OutboundDispatcher`
- [ ] Move current mail send code into `EmailTransport`
- [ ] Insert packet build stage before transport
- [ ] Create `OutboundJournal`
- [ ] Record one journal row per send attempt

## 11.2 Android mandatory tasks

- [ ] Create `MessageIngress`
- [ ] Create `EmailIngress`
- [ ] Create `IncomingMessageParser`
- [ ] Create `EmailMessageParser`
- [ ] Create `UnifiedMessageEntity`
- [ ] Create `MessageSyncStateEntity`
- [ ] Create `UnifiedMessageDao`
- [ ] Create `MessageSyncStateDao`
- [ ] Create `UnifiedMessageRepository`
- [ ] Create `MessageSyncStateRepository`
- [ ] Create `MessageSyncCoordinator`
- [ ] Make `TaskDetailViewModel` read repository instead of raw email
- [ ] Save global email cursor locally
- [ ] Implement local-first then incremental-sync refresh flow

## 11.3 Explicit do-not-do list for this phase

- [ ] Do not implement WebSocket yet
- [ ] Do not implement background push yet
- [ ] Do not implement full relay protocol yet
- [ ] Do not implement encryption yet
- [ ] Do not let UI know transport details
- [ ] Do not cache rendered Android views
- [ ] Do not put email-specific fields into `TaskRunPacket`

---

## 12. Acceptance criteria for the whole phase

This phase is complete when all of the following are true:

1. Existing email workflow still functions.
2. PC business logic no longer sends email directly.
3. PC produces a stable packet before transport.
4. Android no longer feeds Task Detail directly from raw email structures.
5. Android stores unified messages locally.
6. Refresh path becomes: local read first, then incremental sync.
7. Re-entering a task page does not require full historical re-parse.
8. Future relay support can be added by implementing new transport / ingress / parser modules rather than rewriting business and UI layers.

---

## 13. Recommended next document after this one

After this tasklist, the next useful artifact should be one of these:

1. **Class skeleton document**
   - file-by-file constructor fields
   - method signatures
   - dependency direction

2. **Phase-1 implementation checklist**
   - exact order of files to create
   - exact order of code migration
   - verification steps after each migration

For the current project state, document 2 is probably the most practical next step.

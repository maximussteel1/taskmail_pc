# Multimedia Mail Design

> 文档层级：Layer 1（当前仓库正式协议）
>
> 当前 canonical 路径：`docs/current/multimedia_mail_protocol.md`
> 已归档旧来源文件：`docs/archive/multimedia_mail_design_legacy.md`

## Status

This document is the source of truth for the multimedia mail feature agreed in the 2026-03-14 design discussion.

Scope of this feature:

- inbound mail attachments can be materialized directly into the current `workdir`
- outbound status mail can attach files from arbitrary paths when explicitly declared
- image files can be sent both as normal attachments and as inline HTML previews
- backend interaction is file-based; the mail layer does not guess that free-form text should become an attachment or image

## Goals

- let the user send screenshots, documents, and other files from a phone into the active task workspace
- let Codex / OpenCode operate on those files as normal local files
- let the backend send reports, images, and other artifacts back to the phone in a mail-friendly way
- keep the logic deterministic and inspectable

## Non-Goals

- no free-form text to image inference
- no automatic scanning of arbitrary system paths for outgoing attachments without explicit declaration
- no attempt to pretend the backend has image understanding if the selected profile/backend does not support it
- no multi-user permission model

## High-Level Model

There are two separate flows:

### Inbound media

1. parse MIME parts from the incoming mail
2. save the raw attachment under the thread mail archive for debugging
3. materialize a copy directly into the task `workdir`
4. expose the saved attachment paths to the context/prompt layer
5. let the backend decide what to do with those files

### Outbound media

1. backend writes real files to disk
2. backend declares what should be mailed by writing a manifest
3. the mail layer validates the declared files
4. image files are attached and also rendered inline in HTML
5. non-image files are attached normally

## Inbound Attachment Rules

### Storage policy

Incoming attachments are materialized directly into the active `workdir`.

The user explicitly requested that files be dropped directly into `workdir`, not into a dedicated subdirectory. To reduce collisions, the saved filename must be rewritten with a deterministic prefix.

Recommended filename format:

```text
_mailin_YYYYMMDD_HHMMSS_NNN__original_name.ext
```

Examples:

```text
_mailin_20260314_150501_001__photo.png
_mailin_20260314_150501_002__notes.pdf
```

### Raw archive copy

Each attachment must also be saved under the thread mail archive:

```text
tasks/
  thread_001/
    mail/
      raw_007.json
      raw_007_attachments/
        001_photo.png
        002_notes.pdf
```

The raw archive copy is the debugging source of truth.

### Collision policy

- never overwrite existing files in `workdir`
- always generate a new prefixed filename
- preserve the original filename only in metadata and the suffix after `__`

### Attachment-only replies

When a reply contains attachments but no meaningful new body text:

- default to `CONTINUE_SESSION`
- if the thread is `awaiting_user_input`, default to `ANSWER_QUESTION`

### Prompt/context exposure

The context layer must expose:

- all attachments on the incoming mail
- the materialized `workdir` paths
- attachment content type, original filename, and inline flag

The turn text should include a short deterministic summary block so the backend can see that new local files arrived even if it ignores metadata files.

## Outbound Artifact Rules

### Manifest is the primary protocol

Outgoing attachments are declared by a manifest file:

```text
tasks/thread_001/runs/<task_id>/artifacts/manifest.json
```

Manifest example:

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

### Arbitrary outgoing paths

The user explicitly requested that outgoing files may be sent from any location on disk.

Therefore:

- arbitrary absolute paths are allowed
- they must be explicitly listed in the manifest
- the mail layer must not auto-scan arbitrary locations

### Fallback behavior

If `manifest.json` is missing, the system may fall back to files physically present in:

```text
tasks/thread_001/runs/<task_id>/artifacts/
```

This fallback is only for local convenience and does not imply arbitrary path scanning.

### Missing or invalid files

If a manifest item points to a missing or invalid file:

- skip that file
- do not fail the whole mail send
- include a brief skipped-files note in the status mail body

### Relay owner-lane artifact delivery

Current relay runtime behavior now treats relay `/v1/files` as the only
user-consumable artifact lane for relay-backed outbound delivery.

When `outbound_transport=relay` is enabled with `relay_url + relay_transport_token`:

- every attachable run artifact is uploaded to relay `/v1/files`, regardless of size
- `external_delivery_threshold_mb` does not gate this relay owner lane
- the relay owner lane does not fall back to COS or normal MIME attachments
- the status mail keeps the artifact listed in the single `Artifacts` section
- the status mail adds a separate `External Deliveries` section
- the externally delivered file is omitted from actual MIME attachments

Current relay owner-lane policy:

- relay file-surface upload derives `http(s)://<relay-host>/v1/files` from the configured `ws(s)://<relay-host>/relay`
- relay file-surface upload uses the same Bearer transport token as the relay websocket bootstrap
- successful relay external delivery writes both `artifact_file_binding_index.json` and `external_delivery_index.json`
- if relay `/v1/files` upload fails, runtime keeps sending the status mail, does not silently fall back to MIME attachment, and includes a notice that the file was not attached

Legacy note:

- repo 仍保留非 relay 路径的 threshold/COS external-delivery 实现与配置字段，但它不再是 TaskMail/VPS 主线
- `external_delivery_threshold_mb` 在当前主线下只应读成非 relay 路径的 legacy 配置
- COS presigned URL lifetime 仍是 `7 days`
- COS object key shape 仍是 `mail-runner/<thread_id>/<task_id>/<filename>`
- COS upload 仍使用直连 HTTPS session，不继承 ambient `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`
- 对 `apk` / `ipa` payloads on the COS default domain，runtime 仍会把对象名改写为 `<original_name>.bin` 并发出 user-facing notice

## Inline Image Rules

- image files may be attached and also rendered inline in HTML
- inline image preview is controlled per item by `inline: true`
- the original image remains a normal attachment
- preview and attachment may reference the same source file in MVP
- 为避免邮件客户端把同一图片显示成两份附件，inline preview MIME part 不携带附件文件名
- future optimization may generate compressed preview images, but that is not required for MVP
- static attached `image/svg+xml` follows the same inline-preview path as other previewable attached images
- consumer-side body-image matching is based on `cid:` to attachment `content_id`, not filename/order heuristics
- if a client cannot safely render an inline image or inline SVG, it may degrade to normal attachment display and plain-text fallback without violating the current protocol

Current scope note:

- current canonical behavior is still mail HTML inline preview
- when an image is externally delivered instead of attached, inline preview is suppressed for that mail
- future Markdown-first rendering and non-mail renderer layering are tracked separately in `docs/plans/artifact_markdown_rendering_plan.md`
- until that plan is implemented, this document remains the source of truth for current inline image behavior

## Backend Interaction Contract

The backend contract is file-based, not inference-based.

### What the backend receives

- files already materialized into the workspace
- a prompt section listing incoming attachments
- environment variables pointing to incoming attachment metadata and outgoing artifact locations

Suggested environment variables:

- `MAIL_RUNNER_WORKDIR`
- `MAIL_RUNNER_RUN_DIR`
- `MAIL_RUNNER_INCOMING_ATTACHMENTS_JSON`
- `MAIL_RUNNER_ARTIFACTS_DIR`

Implemented runtime details:

- each CLI run creates `runs/<task_id>/artifacts/`
- each CLI run writes `runs/<task_id>/incoming_attachments.json`
- each completed CLI run now persists `runs/<task_id>/artifacts/artifact_index.json` from the resolved artifact truth, even when no status mail is emitted
- status mail delivery and relay-side projection both read that same durable artifact truth
- when relay file-surface external delivery is used, the runtime also writes `runs/<task_id>/artifacts/artifact_file_binding_index.json`
- when any relay external delivery succeeds, the runtime also writes `runs/<task_id>/artifacts/external_delivery_index.json`
- `RunResult.artifacts_dir` points at `runs/<task_id>/artifacts`
- prompt/runtime hints tell the backend to create outgoing files in `MAIL_RUNNER_ARTIFACTS_DIR`
- legacy COS external-delivery credentials may live in a local-only `mail_config.cos.local.yaml` file

### What the backend must do to send a file

- create the actual file on disk
- write `manifest.json` declaring that file

The mail layer will not interpret plain stdout text as an attachment request.

## Data Model Changes

### MailAttachment

Add a dedicated attachment model:

```python
@dataclass(slots=True)
class MailAttachment:
    filename: str
    saved_path: str
    content_type: str
    size_bytes: int
    content_id: str | None = None
    is_inline: bool = False
    sha256: str | None = None
```

### MailEnvelope

Extend `MailEnvelope` with:

```python
attachments: list[MailAttachment]
```

### RunResult

MVP does not require changing `RunResult`, but it may later gain:

- `artifacts_manifest_file`
- `mailed_files`
- `skipped_artifact_messages`

## Module Responsibilities

### `mail_runner/mail_io.py`

- parse inbound MIME attachments
- save raw attachment payloads
- support text/plain + text/html outgoing bodies
- support normal attachments and inline image MIME parts

### `mail_runner/context_layer.py`

- expose incoming attachment metadata
- expose the materialized `workdir` paths

### `mail_runner/intent_parser.py`

- recognize attachment-only replies as session continuation or question answers

### `mail_runner/task_compiler.py`

- carry inbound attachment paths into snapshot/task context
- append deterministic attachment summary text to the turn content

### `mail_runner/reporter.py`

- render HTML body blocks for inline image preview
- render skipped-attachment notes when needed
- internally maintain a Markdown-first authoring path, while current outbound mail remains plain text + HTML

### `mail_runner/artifact_resolver.py`

- read and validate the outgoing manifest
- resolve outgoing attachments
- classify images vs normal files
- report skipped files

## Config Additions

Planned config fields:

- `incoming_attachment_prefix: "_mailin_"`
- `max_incoming_attachment_mb: 25`
- `max_outgoing_attachment_mb: 20`
- `max_outgoing_total_mb: 35`
- `inline_image_max_mb: 8`
- `allow_arbitrary_outgoing_paths: true`

## MVP Acceptance

MVP is complete when all of the following work:

1. a reply mail with an image attachment saves that image into the active `workdir`
2. the saved path is visible to the context/prompt chain
3. an attachment-only reply continues the existing session
4. a backend-generated manifest can reference an arbitrary absolute file path
5. outgoing image files are sent both as attachments and as inline HTML previews
6. missing manifest files or missing declared files do not crash the mail send

## Test Plan

The test suite must cover:

- inbound attachment parsing
- inline image detection
- `workdir` materialization and filename rewriting
- attachment-only reply semantics
- context propagation of attachment paths
- outgoing manifest validation
- arbitrary absolute path attachment sending
- inline image HTML generation
- skipped-file behavior
- end-to-end status mail generation with attachments

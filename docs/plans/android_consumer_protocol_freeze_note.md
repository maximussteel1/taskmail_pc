# Android Consumer Protocol Freeze Note

## Status

- Date: 2026-03-19
- Scope: PC-side freeze note for outbound contract details that Android/Thunderbird now depends on
- Layer: Layer 2 repository note

This note is intentionally short.
It tells the PC-side implementation which outbound details should now be treated as stable for Android consumption while
the current rich-text body slice lands on the Android side.

Current contract authority remains:

- `docs/current/pc_mail_output_protocol.md`
- `docs/current/multimedia_mail_protocol.md`
- `docs/plans/p9_html_mail_projection_plan.md`

## Freeze Now

### 1. Keep `text/plain` As Truth And HTML As Reading Projection

- do not move parser or reply truth into HTML
- keep plain-text machine-readable blocks authoritative
- keep HTML as a reading mirror for Thunderbird for Android and similar clients

### 2. Keep `article.task-mail` As The Consumable Fragment Root

- the semantic HTML body contract for Android consumption is the `article.task-mail` subtree
- outer wrapper markup added for SMTP/mail-client transport should stay transport-only
- do not make Android reverse-engineer arbitrary wrapper structure

### 3. Keep `cid:` Inline Preview Bound To Attachment `contentId`

- inline body images should continue to be referenced through `cid:`
- attachment-side `contentId` is the only matching key Android should need
- do not require filename/order/caption heuristics for body-image resolution
- if transport formatting adds angle brackets around the `Content-ID`, that should be the only normalization Android
  needs to apply

### 4. Freeze Static SVG On The Existing Attached-Image Path

- static attached `image/svg+xml` is the agreed path for formula-like rendered output
- do not introduce a separate formula MIME/protocol path for Android
- keep SVG under the same `inline: true` plus attached-image rule set already used for other previewable images
- if a file is externally delivered instead of attached, keep suppressing inline preview for that mail

### 5. Keep Android V1 Free To Degrade Unsupported HTML Safely

- the PC-side allowed HTML subset should not be read as a guarantee of full Android rendering fidelity in v1
- Android may degrade unsupported blocks to plain text or explicit unsupported placeholders
- do not treat that safe degradation path as protocol breakage

## Freeze Safety Constraints

- no script, animation, iframe, form, or active content in the HTML body contract
- no external fetch dependency for inline SVG or inline image rendering
- meaningful plain-text fallback remains required
- meaningful HTML `alt` text remains required for inline image/SVG preview
- current section ordering and machine-block mirroring should remain stable enough for Android implementation

## Do Not Let These Block The Freeze

The following are valid workstreams, but they should not churn the Android-facing contract while the rich-text body
slice is landing:

- emitted subject-shape changes
- broader neutral outbound-model refactors
- Markdown-first cross-channel renderer convergence
- internal `TaskRunPacket` shape changes

## Change Rule

If any frozen item above must change:

1. update the PC-side current protocol docs first
2. notify the Android side through the paired freeze note or authority doc update
3. only then change the outbound implementation

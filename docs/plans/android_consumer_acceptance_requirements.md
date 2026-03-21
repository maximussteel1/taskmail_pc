# Android Consumer Acceptance Requirements

## Status

- Date: 2026-03-19
- Scope: repository-scoped acceptance requirements for declaring the current Android/Thunderbird consumer validation successful
- Layer: Layer 2 repository plan
- Depends on:
  - `docs/current/pc_mail_output_protocol.md`
  - `docs/current/multimedia_mail_protocol.md`
  - `docs/plans/android_consumer_protocol_freeze_note.md`
  - `docs/plans/p9_html_mail_projection_plan.md`

## Goal

Define the minimum Android-side capabilities, test inputs, and acceptance evidence required before the current outbound
HTML-reading slice can be considered validated.

This document is intentionally narrower than the broader outbound convergence plan.
It is about consumer validation of the frozen contract, not about redesigning the PC-side renderer or mail pipeline.

## What Counts As Acceptance Success

Android-side acceptance is successful when the Android/Thunderbird consumer can read representative task mail from this
repository without requiring further protocol churn from the PC side.

That means all of the following are true:

1. the Android client can consume the current outbound mail shape without requiring subject-shape changes or broader
   outbound-model refactors
2. the Android client can treat HTML as the main reading surface when available, while still respecting `text/plain`
   as the reply/parsing truth source
3. the frozen body contract is proven against representative mails rather than only against inferred or hand-built
   assumptions

## Required Android Consumer Capabilities

The Android side does not need every future feature.
It does need the following minimum capabilities.

### 1. Mail Body Selection

- consume task mail from `multipart/alternative` messages that contain both `text/plain` and `text/html`
- prefer `text/html` for reading when present
- fall back to `text/plain` when the HTML body or semantic fragment is missing or unsafe

### 2. Fragment Root Handling

- treat the `article.task-mail` subtree as the semantic body contract
- ignore outer transport wrapper markup as non-semantic
- do not require the PC side to expose a separate Android-only wrapper or WebView page

### 3. Controlled Rich-Text Rendering

- render the currently allowed HTML subset sufficiently for task-mail reading
- support stable narrow-screen reading for:
  - section headings
  - lists
  - links
  - `pre` / code-like blocks
  - long paths, long tokens, and long lines
- allow safe degradation of unsupported elements to plain text or explicit unsupported placeholders

### 4. Section Recognition

The Android client should be able to recognize and present the current semantic sections when present:

- summary
- meta/context
- task body/details
- artifacts
- external deliveries
- attachment notices
- inline previews
- mirrored machine-readable blocks such as state and question capsules

Acceptance does not require a special renderer per section.
It does require these sections to remain readable and not collapse into an unusable blob.

### 5. Inline Preview Resolution

- resolve inline body images through `cid:` in HTML matched to attachment `contentId`
- allow only angle-bracket normalization around MIME `Content-ID` if needed
- do not depend on filename, ordering, or caption heuristics to match body images to attachments

### 6. Static SVG Handling

- treat attached static `image/svg+xml` previews through the same inline-preview path used for other previewable images
- do not require a separate formula or SVG-specific mail protocol
- degrade cleanly when inline SVG rendering is not available, for example by showing a normal attachment or falling
  back to plain text

### 7. Plain-Text Truth Preservation

- keep reply semantics bound to `text/plain`
- do not require outbound replies to preserve HTML structure
- do not require Android to parse Markdown in order to read or reply to current task mail

## Required Acceptance Inputs

Android acceptance must be executed against representative mail samples, not against a single happy-path mail.

The minimum input matrix should include:

- `DONE` mail with `Artifacts`
- `DONE` mail with inline preview image
- `DONE` mail with static attached SVG preview
- `DONE` mail with `External Deliveries`
- mail with `Attachment Notices`
- `QUESTION` mail with mirrored question capsule
- `PAUSED` or `STATUS` mail with mirrored state capsule
- `FAILED` mail with long error text or code-like content

Each sample should preserve the real outbound shape from this repository:

- `text/plain` plus `text/html`
- current semantic section ordering
- current capsule markers in plain text
- current MIME attachment shape for inline previews and attachments

## Required Evidence For Successful Acceptance

Acceptance should produce concrete evidence that can be reviewed on the PC-side project without re-running the entire
Android implementation discussion.

Minimum evidence:

- a short checklist showing which representative samples were validated
- screenshots or equivalent capture for narrow-screen reading outcomes
- confirmation that `cid:` preview resolution worked from attachment `contentId`
- confirmation that static SVG followed the same preview path or degraded cleanly
- confirmation that reply flow still falls back to `text/plain` truth
- explicit note of any safe degradations observed on Android v1

## Non-Blocking Differences

The following do not block Android acceptance success for the current repository:

- emitted subject-shape cutover
- broader neutral outbound-model rollout
- summary-first plain-text migration
- perfect visual fidelity for every allowed HTML element
- Android-side Markdown parsing
- a dedicated formula-specific mail path
- a local WebView-based task detail page

## Failure Conditions

Android acceptance should be considered not yet successful if any of the following are true:

- the Android client must ask the PC side to change the frozen `article.task-mail` contract before it can consume task
  mail at all
- inline body images cannot be matched without filename/order heuristics
- attached static SVG requires a separate new protocol path
- representative mails become unreadable on narrow screens
- Android reply handling depends on HTML structure rather than `text/plain`
- the tested sample set is too narrow to cover the current frozen contract

## Exit Condition For This Repository

For this repository, Android consumer validation can be treated as complete when:

1. repository-side regression tests cover the frozen outbound HTML/MIME rules
2. Android-side evidence satisfies the requirements above
3. no additional protocol churn is required for the Android client to continue implementation

At that point, the current repository can reasonably move from the narrow `P9` validation slice toward the broader
outbound convergence work without re-opening the Android consumer contract by default.

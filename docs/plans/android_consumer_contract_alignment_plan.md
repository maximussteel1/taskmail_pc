# Android Consumer Contract Alignment Plan

## Status

- Date: 2026-03-19
- Scope: repository-scoped execution adjustment for the current outbound mail work in `mail_based_task_manager`
- Layer: Layer 2 repository plan
- Relation to existing docs:
  - `docs/current/pc_mail_output_protocol.md` remains the Layer 1 outbound contract to converge toward.
  - `docs/plans/p9_html_mail_projection_plan.md` remains the narrow first-round HTML-reading slice.
  - `docs/plans/outbound_mail_contract_convergence_plan.md` remains the broader long-term convergence plan.

## Goal

Adjust the current PC-side development order so that the outbound mail contract needed by Thunderbird for Android is
frozen and validated before broader outbound refactors or less urgent compatibility changes consume the same bandwidth.

This plan is about sequencing and acceptance focus. It does not replace the current outbound contract docs, and it does
not redefine the long-term convergence direction.

## Why Adjustment Is Needed

The current repository now has two correct but differently sized outbound tracks:

1. a narrow P9 HTML-reading improvement slice, and
2. a broader outbound convergence/refactor path.

That split is good and should be preserved.

However, the Android side has now made two important implementation decisions:

- Android Task Detail will use controlled rich-text rendering rather than a local WebView page.
- Formula display is expected to arrive through PC-side rendering to static SVG, then mailed as an inline-previewable
  image artifact with plain-text fallback.

Those decisions change what is most time-sensitive on the PC side.

The Android consumer does not urgently need:

- subject-emission changes,
- a full Markdown-first renderer convergence cutover, or
- the complete neutral outbound model before any HTML/body work lands.

It does urgently need:

- a stable HTML fragment contract,
- frozen inline-image rules,
- an explicit SVG policy, and
- acceptance criteria written from the perspective of Thunderbird/mobile reading.

## Keep As-Is

The following current planning choices are still correct and should not be overturned:

- P9 remains intentionally narrow and focused on HTML reading quality.
- `text/plain` remains the parser/reply truth source.
- `text/html` remains a display mirror, not the only truth source.
- artifact truth stays separate from mail-only projection details such as `cid:`.
- the broader outbound convergence plan remains the right long-term destination.
- same-workspace targeted session commands remain valid current protocol work, but they do not need to block the HTML
  consumption contract freeze.

## Adjusted Priorities

### 1. Freeze the Android/Thunderbird-consumable HTML contract first

Before broader refactors, freeze the HTML details that the Android client actually needs to consume:

- top-level fragment shape,
- section ordering,
- stable class/section naming,
- allowed HTML subset,
- machine-readable block mirroring strategy,
- inline preview expectations for attached images,
- narrow-screen readability for long status mail.

This should be treated as a consumer contract freeze, not as a cosmetic theme pass.

### 2. Add an explicit SVG inline-preview rule to the current docs

The current docs already allow inline image preview through `cid:` and attached image parts.
That is the correct place to carry formula-rendered SVG for Android/mobile consumption.

The current docs should be updated in a follow-up to make the SVG rule explicit:

- SVG used for inline preview must be static and sanitized.
- SVG must not rely on scripts, remote fetches, or active content.
- plain-text fallback and meaningful alt/caption text remain required.
- if an SVG artifact is externally delivered instead of attached, inline preview is suppressed for that mail.

This should be documented as part of the current mail-reading contract, not deferred into future renderer-layering
speculation.

### 3. Add Android/Thunderbird acceptance as a first-class P9 exit criterion

Current P9 wording is directionally correct but still too generic on consumer validation.

Add explicit acceptance coverage for:

- Thunderbird/mobile readability on narrow screens,
- stable rendering of `pre`, `code`, and long lines,
- visible `Artifacts`, `External Deliveries`, and attachment notices,
- inline image preview behavior,
- readable mirrored state/question/run-result blocks,
- graceful reading when HTML is present and plain text remains the reply truth source.

This acceptance should exist both as repository tests where possible and as a small real-mailbox smoke checklist.

### 4. Implement the narrow P9 HTML improvement slice before broad convergence work

After the contract freeze above, continue with the current P9 implementation path:

- keep `multipart/alternative`,
- keep current CID/MIME behavior for inline previews,
- improve the HTML projection for reading quality,
- avoid expanding the slice into a whole-repository rendering architecture rewrite.

### 5. Continue the broader outbound convergence work only after the body contract is stable

The broader convergence plan is still worth doing, but it should follow the body/consumer contract freeze rather than
compete with it.

That means the following become second-wave work after the Android-facing contract is validated:

- neutral outbound model introduction and rollout,
- summary-first plain-text migration,
- fragment-vs-wrapper responsibility split between reporter and `mail_io.py`,
- deeper cleanup of legacy rendering paths.

### 6. Defer emitted subject-shape changes behind body-contract validation

Changing emitted subject shape is orthogonal to the current Android HTML-reading need.

Parser compatibility work for old/new subject formats should continue as planned, but the emitted subject cutover should
not become a dependency for:

- HTML fragment stabilization,
- inline-image/SVG contract freeze, or
- Android/mobile consumer implementation.

## Recommended Execution Order

1. freeze HTML fragment structure, section ordering, and allowed subset for Android/Thunderbird consumption
2. freeze the current SVG-as-inline-image rule and its safety constraints
3. add Android/Thunderbird-oriented acceptance checks to P9 and related tests
4. land the narrow P9 HTML-reading improvements
5. resume the broader outbound convergence work
6. perform emitted subject-shape cutover after the body contract is already proven

## Required Follow-Up Doc Updates

This plan does not perform those contract updates itself.
It recommends the next documentation/code pass make the following changes:

- `docs/current/pc_mail_output_protocol.md`
  - make the SVG inline-preview rule explicit
  - make Android/Thunderbird consumption an explicit reading target
- `docs/current/multimedia_mail_protocol.md`
  - confirm SVG fits the current inline-image path when attached
  - keep external-delivery suppression behavior explicit
- `docs/plans/p9_html_mail_projection_plan.md`
  - add Android/Thunderbird consumer acceptance language
- `docs/plans/outbound_mail_contract_convergence_plan.md`
  - explicitly sequence subject cutover behind body-contract validation

## Non-Goals

- Do not expand P9 into a full Markdown-first cross-channel renderer convergence project.
- Do not push mail-only fields such as `cid:` into artifact truth-layer files.
- Do not block current same-workspace targeted-command work on the Android HTML-reading effort.
- Do not require Android clients to parse Markdown in order to render or reply to current task mail.

## Done When

- the current docs explicitly define the Android/Thunderbird-consumable HTML and inline-image contract,
- SVG-as-inline-image is no longer an implicit assumption,
- P9 acceptance explicitly includes Thunderbird/mobile reading outcomes,
- the broader outbound convergence plan is still preserved, but sequenced after the body contract freeze,
- emitted subject-shape changes are no longer implicitly competing with the more urgent consumer-contract work.

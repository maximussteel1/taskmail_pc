# Outbound Mail Baseline And Delta Checklist

## Status

- Date: 2026-03-19
- Scope: implementation-readiness baseline for current outbound task mail
- Layer: Layer 2 repository plan / Phase 0 inventory
- Related docs:
  - `docs/current/pc_mail_output_protocol.md`
  - `docs/current/multimedia_mail_protocol.md`
  - `docs/plans/p9_html_mail_projection_plan.md`
  - `docs/plans/android_consumer_protocol_freeze_note.md`
  - `docs/plans/outbound_mail_contract_convergence_plan.md`

## Purpose

Turn the current-vs-target outbound diff into one explicit checklist before code changes start.

This document is the concrete Phase 0 baseline matrix referenced by
`docs/plans/outbound_mail_contract_convergence_plan.md`.

## Status Tags

- `aligned-now`: current runtime already matches the frozen contract closely enough to keep as-is.
- `p9-scope`: should land as part of the narrow Thunderbird/Android HTML-reading slice.
- `post-p9`: should remain deferred to broader outbound convergence.
- `watch`: not an immediate code change, but must stay visible while refactors happen.

## Current Output Chain Baseline

1. `mail_runner/app.py` resolves artifacts, external deliveries, and attachment notices, then builds one Markdown body through `reporter.build_status_markdown()`.
2. The same Markdown body is projected to `text/plain` by `render_status_markdown_to_plain_text()` and to `text/html` by `build_status_html()`.
3. `mail_runner/mail_io.py` assembles MIME, adds `text/plain`, optionally adds `text/html`, and binds inline image attachments to HTML through `Content-ID`.

This means the repository already has one outbound projection pipeline, but the pipeline still mixes:

- current operator-facing text conventions,
- current HTML wrapper shape,
- and the newer Android-facing consumer contract.

## Baseline Matrix

### 1. Subject Shape And Routing

- `aligned-now` Reply routing priority remains `In-Reply-To` -> `References` -> state capsule -> `[S:<session_id>]` subject fallback. This must not change in P9.
- `post-p9` Emitted subject shape is still built as `[TAG][S:<session_id>] <display title>` in `reporter.build_status_subject()`.
- `post-p9` The current protocol recommends `[TAG] <display title> [S:<session_id>]`.
- `watch` Parser compatibility for both old and new subject layouts should be preserved during any later cutover.

### 2. Plain-Text Body Contract

- `post-p9` Current plain text is still operator-heavy because `_build_status_markdown()` starts with `Status:`, `Session ID:`, `Thread ID:`, `Task ID:`, `Backend:`, `Permission:`, `Repo:`, and `Workdir:`.
- `post-p9` Current protocol recommends a summary-first order: `Summary`, `Permission`, `Reply`, then `Artifacts`, `External Deliveries`, `Attachment Notices`, run-result block, state capsule, and question capsules.
- `post-p9` Current running `/status` replies still prefer live reasoning/internal stream summaries inside that operator-heavy body; the target direction is `Summary: Running.` plus `Reply:` with the latest assistant-visible output, or `Reply: No assistant output yet.` when no assistant output exists yet.
- `aligned-now` Section naming is now aligned on one `Artifacts` section plus a separate `Attachment Notices` section, and current tests already lock this in.
- `aligned-now` `External Deliveries` is already a separate projected section and should stay separate.
- `post-p9` Terminal receipts still flatten run-result fields into human-readable lines such as `Exit Code`, `Tests Passed`, `Changed Files`, `Error Type`, and `Error`, rather than emitting the structured run-result block required by current protocol.

### 3. State And Question Capsules

- `aligned-now` Reporter always appends one state capsule for task-related system mail.
- `aligned-now` Waiting-state mail appends question capsules when there are pending questions.
- `aligned-now` These capsule blocks remain parser-facing truth and should not be displaced by HTML-first rendering.
- `p9-scope` HTML should gain a clearer semantic mirror for capsule blocks instead of leaving them inside the generic text projection.

### 4. HTML Semantic Body

- `p9-scope` Current HTML is still emitted as a full `<html><body>...</body></html>` document by `render_status_markdown_to_html()` / `build_status_html()`.
- `p9-scope` The frozen consumer contract wants a stable `article.task-mail` semantic subtree with ordered summary/meta/body/artifacts/external-deliveries/inline-preview content.
- `p9-scope` Current HTML mostly projects headings, bullet lists, generic text, and inline figures, but it does not yet build the semantic section structure described by `docs/current/pc_mail_output_protocol.md`.
- `p9-scope` Current HTML should be treated as the baseline to replace for Thunderbird for Android reading, not as the target to preserve.

### 5. HTML Fragment Versus Transport Wrapper

- `watch` Android's consumer-facing contract is defined on the `article.task-mail` subtree, not on any outer transport wrapper.
- `p9-scope` The practical first P9 milestone is to produce and stabilize that subtree in outbound HTML.
- `post-p9` The deeper boundary cleanup where reporter emits only a reusable fragment and `mail_io.py` owns wrapper concerns can stay under broader outbound convergence if the P9 slice can land safely first.

### 6. Inline Images And Attachment Binding

- `aligned-now` Reporter already assigns inline `content_id` values when needed and emits `cid:` references for inline image previews.
- `aligned-now` `mail_io.py` already turns those inline images into related MIME parts using matching `Content-ID` values.
- `aligned-now` This matches the frozen rule that Android/mobile consumers should bind `cid:` references to attachment `contentId`, not filename or order heuristics.
- `p9-scope` P9 should preserve this rule while improving the semantic HTML around inline previews.

### 7. Artifacts, External Deliveries, And Attachment Notices

- `aligned-now` `app.py` resolves artifacts, writes the artifact index, projects outgoing attachments, then splits oversized artifacts into `External Deliveries` while keeping attachment notices separate.
- `aligned-now` Reporter and tests already enforce one `Artifacts` section, followed by `Attachment Notices` if present.
- `p9-scope` HTML rendering still needs to turn these sections into the stable consumer-facing structure expected by Android/Thunderbird.
- `aligned-now` Artifact truth remains outside mail-only fields; no `cid:` or transport-only fields have been pushed back into `artifact_index.json`.

### 8. MIME Structure

- `aligned-now` `mail_io.py` already emits `text/plain`, optionally emits `text/html`, and adds inline images as related parts to the HTML alternative.
- `aligned-now` This is already compatible with the required `multipart/alternative` plus inline-image `multipart/related` behavior.
- `watch` P9 and post-P9 refactors should avoid regressing this MIME behavior while HTML structure changes.

### 9. Stored Outgoing HTML Shape

- `watch` Current outgoing mail persistence stores the HTML body generated before SMTP send, which today is still a full HTML document.
- `watch` If reporter later moves toward fragment-first generation, the repository must explicitly decide whether stored outgoing records keep the full transport wrapper, the semantic fragment, or both.

## Immediate Execution Split

### P9 Scope

- add the stable `article.task-mail` subtree and ordered HTML section structure,
- improve narrow-screen HTML readability for Thunderbird for Android,
- preserve `text/plain` as reply/parsing truth,
- preserve current artifact truth boundaries,
- preserve `cid:` to attachment `contentId` matching,
- keep state and question capsules present and clearly mirrored in HTML.

### Post-P9 Scope

- summary-first plain-text rewrite,
- structured run-result block in `text/plain` and mirrored HTML,
- emitted subject-shape cutover,
- neutral outbound model / `ReporterEnvelope`-style convergence,
- final reporter-fragment versus `mail_io`-wrapper cleanup if not already required by the P9 landing.

## Pre-Implementation Checklist

- inventory any tests or tooling that still assert the old `Status:` / `Session ID:` / `Repo:` plain-text header.
- add golden tests for the current subject shape so the later cutover is explicit.
- add P9 tests for `article.task-mail`, section ordering, and Android-safe HTML subset rules.
- verify that Thunderbird for Android acceptance does not require parser-facing run-result changes in the first HTML slice.
- keep the Android-facing contract freeze documents authoritative while code is still catching up.

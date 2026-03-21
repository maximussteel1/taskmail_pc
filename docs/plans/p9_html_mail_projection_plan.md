# P9 HTML Mail Projection Plan

## Status

- Date: 2026-03-19
- Review date: 2026-03-20
- Status: partially landed in repository code/tests; temporarily frozen
- Scope: narrow Phase 9 direction for the current repository
- Goal: make outbound status mail easier to read in Thunderbird for Android and similar narrow-screen mail clients by strengthening the existing HTML projection layer
- Execution baseline: `docs/plans/outbound_mail_baseline_delta_checklist.md`
- Paired acceptance reference: `docs/plans/android_consumer_acceptance_requirements.md`

## Freeze Notice

As of 2026-03-20, P9 is temporarily frozen.

- Keep the repo-side HTML projection work that is already landed.
- Do not keep expanding P9-scope HTML work while the repository reprioritizes the next layering and stable-connection direction.
- Treat this document as both the frozen plan and the progress snapshot until P9 is explicitly reopened.

## Position

P9 is intentionally narrow.

It does **not** replace the current truth layers:

- Markdown remains the canonical authored body.
- `text/plain` remains the reply/parsing truth source.
- `text/html` is treated as the preferred reading projection for clients that render HTML well, such as Thunderbird on Android.
- Thunderbird for Android is the first consumer to optimize for in this phase.

## Why This Scope

- The repository already emits both `text/plain` and `text/html`.
- Thunderbird/mobile reading UX can improve without changing the current mail protocol.
- Android now has a concrete controlled-rich-text reading path, so freezing the HTML consumer contract is more urgent than broader outbound refactors.
- Narrowing P9 avoids turning a practical mail-reading improvement into a broader renderer architecture project.

## Planned Direction

1. Keep `multipart/alternative` as the outbound mail shape.
2. Improve the HTML projection generated from the existing Markdown body.
3. Make the HTML output more comfortable for narrow screens and long status mails.
4. Continue projecting inline image previews through the existing CID/MIME path.
5. Treat static attached SVG previews, including formula-rendered output, as part of the existing inline-image path rather than a separate protocol.
6. Keep reply handling and command parsing bound to `text/plain`.

## Phase-0 Split Applied To P9

Based on `docs/plans/outbound_mail_baseline_delta_checklist.md`, P9 should explicitly take these deltas:

- add the stable `article.task-mail` semantic subtree,
- add ordered HTML sections for summary/meta/body/artifacts/external deliveries/attachment notices,
- improve narrow-screen HTML readability,
- preserve `cid:` to attachment `contentId` binding,
- keep state and question capsules present and clearly mirrored in HTML.

P9 should explicitly leave these deltas alone:

- subject-shape cutover,
- summary-first plain-text rewrite,
- structured run-result block introduction,
- neutral outbound model / `ReporterEnvelope`-style convergence,
- deeper reporter-fragment versus `mail_io`-wrapper cleanup beyond what is strictly needed for the Android consumer contract.

## Guardrails

- Do not make HTML the only user-visible or machine-readable source of truth.
- Do not require Android/Thunderbird replies to preserve HTML structure.
- Do not add mail-specific fields such as `cid:` into `artifact_index.json`.
- Do not expand P9 into cross-channel renderer convergence in the first round.
- Do not block the Android/Thunderbird HTML-reading slice on subject-shape changes or broader outbound-model refactors.
- Do not introduce a formula-specific mail protocol when static SVG can travel through the existing attached-image rule set.

## First-Round Deliverables

- Stable `article.task-mail` subtree in outbound HTML, with transport wrapper treated as non-semantic.
- Clearer HTML rendering for sections such as `Artifacts`, `External Deliveries`, `Attachment Notices`, and capsule mirrors.
- Safer narrow-screen rendering for long lines, code-like blocks, and inline previews.
- Refined status-mail HTML styling for Thunderbird for Android and similar narrow-screen mail clients.
- Explicit SVG-as-inline-image guidance for Android/mobile reading, including formula-rendered output carried as static attached SVG.
- Regression coverage in `tests/test_reporter.py`, plus outbound MIME regression coverage in `tests/test_mail_io.py`.
- Android-side validation should be judged against `docs/plans/android_consumer_acceptance_requirements.md`.

## Current Progress Snapshot (2026-03-20)

### Repo-Side Landed

- [x] `mail_runner/app.py` main outbound path now builds one Markdown body and projects it to both `text/plain` and `text/html`.
- [x] `mail_runner/reporter.py` emits the stable `article.task-mail` subtree plus ordered HTML sections for `Artifacts`, `External Deliveries`, `Attachment Notices`, inline previews, and capsule mirrors.
- [x] Inline image previews still bind `cid:` to attachment `contentId`, and static attached SVG follows the same inline-image path rather than a separate protocol.
- [x] `mail_runner/mail_io.py` still sends `multipart/alternative` and preserves inline-image `multipart/related` behavior.
- [x] Subject shape, `text/plain` truth semantics, and the broader summary-first / neutral-model cutover remain unchanged.
- [x] Regression coverage exists in `tests/test_reporter.py` and `tests/test_mail_io.py` for the repo-side HTML/MIME surface.

### Still Open Before P9 Can Be Called Complete

- [ ] Record Android-side validation against `docs/plans/android_consumer_acceptance_requirements.md`.
- [ ] Record client-facing acceptance evidence that Thunderbird for Android can actually treat the emitted HTML as the main reading surface.
- [ ] Publish an explicit closeout or supersession note deciding whether P9 will be resumed, retired, or absorbed into a later layering / transport plan.

### Freeze Re-entry Rule

If P9 is reopened later, start from the remaining acceptance items above. Do not reopen it by adding new HTML scope first.

## Recommended Patch Order

1. Update `mail_runner/reporter.py` so HTML output grows the stable `article.task-mail` subtree and ordered section structure without changing subject emission or plain-text truth.
2. Add explicit HTML mirrors for `Artifacts`, `External Deliveries`, `Attachment Notices`, state capsules, and question capsules while preserving the existing CID inline-image path.
3. Refine narrow-screen-safe styling and static SVG handling for Thunderbird for Android without introducing client-specific hacks or remote resources.
4. Add or update regression tests in `tests/test_reporter.py` for HTML structure and in `tests/test_mail_io.py` for outbound `multipart/alternative` plus inline-image `multipart/related` behavior.

## Acceptance Gates

- HTML contains a stable `article.task-mail` semantic subtree.
- `Artifacts`, `External Deliveries`, and `Attachment Notices` retain stable relative ordering in HTML.
- State and question capsules remain present in `text/plain` and gain explicit HTML mirrors.
- Inline image previews continue to use the existing `cid:` plus attachment `contentId` rule.
- Outbound mail still sends both `text/plain` and `text/html`.
- P9 does not change emitted subject shape.
- P9 does not rewrite `text/plain` into the broader summary-first contract.
- P9 does not introduce the structured run-result block if that work is not otherwise already required by Android acceptance.

## Done When

- Outbound status mail remains `text/plain` + `text/html`.
- Thunderbird for Android can treat HTML as the main reading surface.
- Static attached SVG previews follow the same inline-image rules and degrade cleanly when inline preview is unavailable.
- Reply semantics remain unchanged and continue to rely on `text/plain`.
- Current artifact truth-layer boundaries remain intact.
- The HTML fragment contract is stable enough for Android implementation without waiting for broader outbound convergence work.
- The patch sequence and acceptance gates above can be used directly as the first implementation checklist.

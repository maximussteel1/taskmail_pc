# Outbound Mail Contract Convergence Plan

## Status

- Date: 2026-03-19
- Scope: long-term optimal convergence plan for PC-side outbound task mail in `mail_based_task_manager`
- Layer: Layer 2 repository plan
- Relation to existing docs:
  - `docs/current/pc_mail_output_protocol.md` defines the target outbound contract to align code toward.
  - `docs/plans/p9_html_mail_projection_plan.md` remains the narrower first-round HTML-only slice.
  - `docs/plans/artifact_markdown_rendering_plan.md` remains the renderer-layering reference for artifact and Markdown concerns.

## Goal

Converge outbound task mail onto one stable internal model and one stable external contract, with these selected decisions:

1. `docs/current/*` may freeze the target contract before code fully matches it, but alignment status must stay explicit.
2. Introduce one neutral outbound mail model between task/run state and channel rendering.
3. Move `text/plain` to the summary-first contract defined in `docs/current/pc_mail_output_protocol.md`.
4. Make reporter output an HTML fragment, while `mail_io.py` wraps that fragment for SMTP/mail-client compatibility.
5. Change emitted subject shape to the new protocol form while keeping parsers compatible with both old and new subject formats.

This is broader than P9. P9 improves HTML reading quality without changing the deeper rendering boundary. This plan defines the longer-term end state after that narrower slice, or the broader route if the repository explicitly chooses to implement the full convergence path.

Execution prerequisite:

- do not start Phase 1 neutral-model rollout until the Android-facing consumer contract freeze defined by `docs/current/pc_mail_output_protocol.md`, `docs/current/multimedia_mail_protocol.md`, `docs/plans/p9_html_mail_projection_plan.md`, and `docs/plans/android_consumer_protocol_freeze_note.md` is accepted.

## Non-Goals

- Do not change reply routing priority away from `In-Reply-To`, `References`, state capsule, then subject fallback.
- Do not make `text/html` the only truth source for users or parsers.
- Do not push mail-only fields such as `cid:` into `artifact_index.json`.
- Do not rewrite scheduler, queueing, thread lifecycle, or backend resume semantics as part of this plan.
- Do not require Android or other clients to preserve HTML structure in replies.
- Do not remove the existing Markdown path until the replacement model and projections are fully covered by tests.

## Chosen Technical Direction

### 1. Documentation Alignment Policy

Use the current repository rule already stated in `docs/current/README.md`: protocol docs in `docs/current/*` may represent the current contract being aligned, not only behavior that is already byte-for-byte implemented.

Implication:

- code can move in safe phases without blocking on one big-bang cutover,
- but every planned mismatch between docs and runtime must be intentional and short-lived,
- and `docs/current/pc_mail_output_protocol.md` must be treated as the contract to converge toward, not a loose design note.

### 2. Neutral Internal Outbound Model

Introduce one neutral outbound model. This plan uses the working name `StatusMailModel`.

The final code name may remain `ReporterEnvelope` if that better matches `docs/current/reporter_output_skeleton.py`, but the role must be the same:

- reporter input is task/run/question/artifact state,
- reporter output is one typed status-mail model,
- channel-specific projections derive from that model,
- MIME assembly stays outside the reporter.

Required responsibilities of the model:

- visible tag and subject inputs,
- summary/meta fields,
- artifact list,
- external delivery list,
- attachment notices,
- run-result projection,
- state capsule projection,
- question capsule projection,
- mail-only inline attachment mapping inputs.

### 3. Summary-First Plain Text Contract

The end state for `text/plain` should follow the ordering in `docs/current/pc_mail_output_protocol.md`, rather than the current operator-heavy dump.

Target top-level order:

1. `Summary`
2. `Permission`
3. `Reply` or waiting/resume hint
4. `Artifacts`
5. `External Deliveries`
6. `Attachment Notices`
7. run-result block
8. state capsule
9. question capsules

Current lines such as `Status:`, `Session ID:`, `Task ID:`, `Backend:`, `Repo:`, and `Workdir:` should leave the primary human-readable body. If some of them still matter for operators, they should either:

- remain available in the state capsule, or
- temporarily live in a clearly secondary compatibility section during rollout.

Special rule for `/status` current-state queries while a session is running:

- the primary human-readable body should lead with the fact that the session is currently running,
- the next human-readable field should be the latest assistant-visible output from that session,
- if no assistant output is available yet, the reply should explicitly say that the session is running and that no assistant output is available yet,
- reasoning-only summaries, tool-step lines, or other operator-facing live diagnostics should not replace the assistant-visible output in this specific `/status` view.

Recommended plain-text shape for running `/status` replies:

```text
Summary: Running.
Reply:
<latest assistant output>
```

Fallback when no assistant output is available yet:

```text
Summary: Running.
Reply: No assistant output yet.
```

Relay compatibility boundary for this running `/status` shape:

- this body shape is intentionally transport-neutral and should remain the same whether the user-facing mail is sent directly by the PC or delivered through the VPS relay,
- it is suitable for the common "the session is still running, what was the latest assistant-visible output?" query,
- it is not sufficient as a liveness proof for the PC host itself,
- if users need visibility when the PC host is offline, stuck, or has not consumed the `/status` command yet, that requires a separate VPS-visible last-known-health projection rather than a different running `/status` body.

### 4. HTML Fragment Plus Transport Wrapper

Reporter should render an HTML fragment shaped for reuse outside SMTP:

```html
<article class="task-mail">...</article>
```

`mail_io.py` should then wrap that fragment into the minimal mail-safe HTML document needed for `EmailMessage.add_alternative(...)`.

This keeps responsibilities clean:

- reporter owns fragment structure and semantics,
- `mail_io.py` owns SMTP/MIME compatibility,
- Android/web preview can reuse the fragment without reverse-engineering a mail-client document shell.

### 5. Subject Compatibility Strategy

Emitter target:

```text
[TAG] <display title> [S:<session_id>]
```

Compatibility requirement:

- subject parsing and normalization must accept both the old emitted form and the new emitted form,
- routing must continue to prefer headers and capsules over subject,
- tests must prove that changing the emitted subject does not change thread/session resolution behavior.

## Target Architecture

Recommended outbound path:

```text
thread state + task snapshot + run result + artifacts + pending questions
    -> StatusMailModel
    -> Markdown projection (internal authored view, optional but supported)
    -> Plain-text projection (protocol-safe fallback)
    -> HTML fragment projection (preferred reading surface)
    -> Inline attachment map projection
    -> mail_io transport wrapper + MIME assembly
```

Key boundary rules:

- `artifact_index.json` stays artifact-truth-layer only.
- `OutgoingAttachment.content_id` is assigned at mail projection or transport time, not in artifact truth.
- Capsules remain parser-facing text blocks and are mirrored into HTML only as display projections.
- Reporter does not directly decide MIME nesting or SMTP concerns.

## Implementation Phases

### Phase 0: Baseline And Delta Inventory

Purpose:

- make the current-vs-target diff explicit before refactoring.

Work:

- enumerate all current outbound shapes that differ from `docs/current/pc_mail_output_protocol.md`,
- list every user-visible field currently relied on by tests,
- identify any parsing or external tooling that depends on current `Status:` / `Session ID:` lines,
- confirm which stored raw-mail fields should persist full HTML documents versus reusable fragments.

Phase 0 artifact:

- `docs/plans/outbound_mail_baseline_delta_checklist.md`

Done when:

- the repository has an explicit baseline matrix for subject, plain text, HTML, attachments, and capsules,
- and the Android-facing consumer contract freeze has been treated as an input constraint rather than something still open for redesign.

### Phase 1: Introduce The Neutral Model Without User-Visible Change

Purpose:

- create the new boundary without immediately changing mail content.

Work:

- add `StatusMailModel` (or equivalent final name),
- have reporter build the model from current inputs,
- keep current Markdown/plain/html output behavior as compatibility projections from that model,
- keep existing artifact and inline-image behavior unchanged,
- add unit tests proving projection parity with the current baseline.

Done when:

- outbound mail is generated from the new model internally,
- but current visible behavior is still intentionally unchanged.

### Phase 2: Make All Projections Model-Driven

Purpose:

- remove ad hoc rendering paths and centralize outbound semantics.

Work:

- derive Markdown, plain text, HTML fragment, and inline attachment map from the same model,
- stop mixing rendering decisions directly into `app.py`,
- isolate mail-only delivery decisions from artifact resolution,
- keep `artifact_resolver.py` focused on artifact truth and attachment projection inputs.

Done when:

- `app.py` assembles data once and asks reporter for projections,
- reporter no longer needs parallel custom logic for separate output formats.

### Phase 3: Switch Plain Text To The Summary-First Contract

Purpose:

- align the human-readable fallback body with the new outbound protocol.

Work:

- replace the current operator-heavy body shape with the summary-first contract,
- preserve run-result, state capsule, and question capsule blocks,
- migrate any truly necessary operator/debug facts out of the primary body,
- update tests to assert section order and single-section invariants.

Done when:

- `text/plain` matches the new contract for `[RUNNING]`, `[QUESTION]`, `[DONE]`, `[FAILED]`, `[KILLED]`, and `[PAUSED]` mails,
- and reply/parsing truth remains intact.

### Phase 4: Switch HTML To Fragment Rendering And Mail-Side Wrapping

Purpose:

- align HTML with the reusable fragment contract while preserving mail compatibility.

Work:

- make reporter emit fragment-only HTML,
- move outer mail-document wrapping into `mail_io.py`,
- keep `multipart/alternative` and `multipart/related` semantics correct for inline images,
- ensure fragment sections match the stable class/section layout from `docs/current/pc_mail_output_protocol.md`,
- add MIME-structure regression tests.

Done when:

- the stored/rendered reporter output is a fragment,
- and mail transport still sends a compatible HTML alternative with inline previews.

### Phase 5: Change Subject Emission While Keeping Dual-Format Parsing

Purpose:

- align emitted subject lines without breaking routing compatibility.

Work:

- update subject builder to emit the new subject shape,
- keep subject normalizer compatible with old and new shapes,
- add tests for same-thread routing across mixed historical subject shapes.

Done when:

- the repository emits the new subject shape by default,
- but old archived mails and reply chains remain routable.

### Phase 6: Cleanup, Documentation Convergence, And Live Acceptance

Purpose:

- finish the refactor and remove transitional scaffolding.

Work:

- remove legacy compatibility branches that are no longer needed,
- update `docs/current/README.md` to list the new current outbound protocol docs if not already listed,
- update current docs to remove any wording that still assumes the old body shape,
- run targeted real-mailbox acceptance for:
  - plain summary-first mail,
  - HTML fragment wrapped for mail,
  - question mail with capsules,
  - terminal receipt with artifacts and external deliveries,
  - mixed historical subject compatibility.

Done when:

- docs, code, tests, and live mailbox behavior describe the same outbound contract.

## File-By-File Work Areas

### `mail_runner/reporter.py`

- introduce and own the neutral model,
- generate projections from one source,
- stop emitting full HTML documents,
- make section ordering explicit and testable.

### `mail_runner/app.py`

- collect state/run/artifact/question inputs once,
- assemble the neutral model once,
- request projections from reporter instead of piecemeal rendering,
- keep send/store logic separate from rendering concerns.

### `mail_runner/mail_io.py`

- accept fragment HTML and wrap it for SMTP transport,
- preserve `multipart/alternative` and inline-image `multipart/related` behavior,
- keep header and attachment assembly here rather than in reporter.

### `mail_runner/artifact_resolver.py`

- keep artifact truth-layer behavior stable,
- keep `artifact_index.json` mail-agnostic,
- continue projecting attachment inputs without introducing `cid:` or mail-only fields.

### `mail_runner/state_capsule.py`

- remain the canonical renderer/parser for state and question capsule blocks,
- avoid duplicating capsule serialization logic inside reporter.

### `tests/test_reporter.py`

- cover model construction,
- cover plain-text section order,
- cover HTML fragment output,
- cover artifact/external-delivery/attachment-notice rendering,
- cover old/new subject compatibility behavior at the reporter boundary when relevant.

### `tests/test_mail_io.py`

- cover fragment wrapping,
- cover `multipart/alternative` plus `multipart/related` structure,
- cover inline image + normal attachment coexistence,
- cover safe handling of empty/no-HTML cases.

### `tests/test_app_phase*.py`

- cover app-level propagation from run result and artifact resolution into sent mail,
- cover no-regression behavior for waiting-state and receipt mails,
- cover mixed historical subject-chain compatibility if subject emission changes in app-level flows.

## Risks And Mitigations

### Risk 1: Hidden Dependence On Current Plain-Text Fields

Mitigation:

- inventory current fields in Phase 0,
- keep one short-lived compatibility bridge if necessary,
- rely on capsules for parser-facing facts.

### Risk 2: HTML Fragment Compatibility Differences Across Mail Clients

Mitigation:

- keep wrapper responsibility in `mail_io.py`,
- add MIME tests plus real mailbox smoke tests,
- avoid over-optimizing around client-specific hacks in the first pass.

### Risk 3: Confusion Between Markdown Truth And The Neutral Model

Mitigation:

- explicitly define the neutral model as the internal rendering truth,
- treat Markdown as an authored/view projection, not the cross-layer protocol,
- remove ambiguous comments once the new model lands.

### Risk 4: Attachment And Inline Preview Regressions

Mitigation:

- keep `artifact_resolver.py` boundaries stable,
- keep `cid` generation out of artifact truth,
- test inline and attached image paths at both reporter and MIME levels.

## Recommended Execution Rule

This plan should be implemented as additive phases until Phase 3 or 4 introduces the visible contract flip.

In practice:

- do not mix Phase 1 model introduction with Phase 3 body-shape changes in one patch,
- do not mix Phase 4 HTML fragment conversion with unrelated scheduler or reply-parser changes,
- and do not change `artifact_index.json` semantics as part of this plan.

## Done When

The long-term optimal version is complete when all of the following are true:

- outbound system mail is assembled from one neutral model,
- `text/plain` follows the summary-first contract,
- reporter output HTML is a reusable fragment and `mail_io.py` owns transport wrapping,
- emitted subjects use the new format while parsers accept both formats,
- `artifact_index.json` remains mail-agnostic,
- capsules remain the parser-facing truth blocks,
- and current docs plus code agree on the same outbound contract.

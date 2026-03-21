# PC Outbound Layering First Slice Checklist

## Status

- Date: 2026-03-20
- Scope: execution-level checklist for the first coding slice of `docs/plans/pc_outbound_layering_refactor_plan.md`
- Slice boundary:
  - cover Phase 0 plus the smallest useful part of Phase 1
  - do **not** introduce packet or transport abstractions yet

## Goal Of This Slice

Land the first extraction safely:

1. move the current outbound status-mail composition out of `mail_runner/app.py`,
2. keep the current outbound contract and visible behavior unchanged,
3. create a small renderer seam so later packet / transport work has somewhere clean to attach.

## Exact In-Scope Work

- extract `_send_status_update(...)` out of `mail_runner/app.py`
- move the directly coupled helper cluster with it when needed to avoid circular imports:
  - `_default_reply_headers(...)`
  - `_store_outgoing_mail(...)`
  - `_prune_previous_status_mails(...)`
  - `_load_captured_reply(...)`
- keep `artifact_resolver.py`, `external_delivery.py`, `reporter.py`, and `mail_io.py` behavior unchanged in the first pass
- add a thin renderer facade only after the workflow shell extraction is stable

## Explicitly Out Of Scope For This Slice

- `TaskRunPacket`
- `OutboundDispatchRequest`
- `OutboundTransport`
- `EmailTransport`
- `OutboundDispatcher`
- journaling
- relay/VPS transport code
- Android protocol changes
- any HTML/body contract redesign

## File Order

### Pass A: Extract The Workflow Shell Only

1. Add `mail_runner/outbound/__init__.py`.
2. Add `mail_runner/outbound/service.py`.
3. Move the current `_send_status_update(...)` implementation into the new module.
4. Move the tightly coupled helper cluster with it if that is the simplest way to keep imports acyclic.
5. Leave a thin wrapper in `mail_runner/app.py` so callers do not change yet.

Definition of success for Pass A:

- `app.py` still exposes `_send_status_update(...)`,
- but the logic now lives in `mail_runner/outbound/service.py`,
- and the resulting sent mail stays unchanged.

### Pass B: Add The Smallest Renderer Seam

1. Add `mail_runner/outbound/renderer.py`.
2. Optionally add `mail_runner/outbound/normalizer.py` only if a tiny adapter object makes the extracted service clearer.
3. Start with delegation only:
   - subject still comes from `build_status_subject(...)`
   - text fallback still comes from `render_status_markdown_to_plain_text(...)`
   - HTML still comes from `build_status_html(...)`
4. Switch `mail_runner/outbound/service.py` to call the renderer facade instead of calling reporter helpers directly.

Definition of success for Pass B:

- the service no longer assembles subject/plain/html itself,
- but the renderer facade is still just a thin wrapper over existing reporter behavior.

## Guardrails

- Do not change the current signature or visible semantics of `_send_status_update(...)` in the first slice.
- Do not change the subject shape.
- Do not change `text/plain` ordering or truth semantics.
- Do not change the HTML fragment/body contract.
- Do not change how inline attachments, external deliveries, or artifact notices are projected.
- Do not move mailbox pruning or outgoing-mail persistence to a new abstraction yet; only relocate the existing code if needed for extraction.
- Prefer moving code as a cluster over splitting logic across `app.py` and `service.py` with callback plumbing.

## First Tests To Run

Start with targeted tests only.

Core rendering / transport regression:

1. `.venv\Scripts\python.exe -m pytest tests/test_reporter.py`
2. `.venv\Scripts\python.exe -m pytest tests/test_mail_io.py`
3. `.venv\Scripts\python.exe -m pytest tests/test_external_delivery.py`

Representative app-flow coverage:

4. `.venv\Scripts\python.exe -m pytest tests/test_app_phase3.py`
5. `.venv\Scripts\python.exe -m pytest tests/test_app_phase6.py`
6. `.venv\Scripts\python.exe -m pytest tests/test_app_phase6_multi_question.py`

If the first slice changes only extraction boundaries and all targeted tests stay green, defer full-suite execution until the packet or dispatcher layer lands.

## Review Checklist Before Merging The Slice

- `mail_runner/app.py` outbound send logic is materially smaller.
- The extracted module owns the status-mail composition flow.
- No new protocol fields appear in sent mail, artifacts, or stored metadata.
- Existing app tests still see the same subject sequence and reply-header behavior.
- No packet or transport abstraction has been half-introduced.
- The next slice can start from `renderer.py` and `service.py` instead of reopening `app.py`.

# Plans

This directory contains repository-scoped implementation plans for `mail_based_task_manager`.

Rules:

- Documents here should describe changes to the current repository, not the future full platform.
- If `README.md`, `state.md`, or `docs/current/*` disagree with a plan here, treat current behavior as the source of truth.
- Keep speculative platform design out of this directory.

Current plan documents:

- `mail_adapter_refactor_plan.md`: mail adapter refactor plan for the current repository.
- `run_artifact_delivery_plan.md`: local file delivery and run-artifact consolidation plan.
- `artifact_markdown_rendering_plan.md`: Markdown-first artifact rendering and inline-image layering plan.
- `backend_permission_control_plan.md`: `Permission` field semantics and backend projection plan.
- `project_folder_sync_entry_plan.md`: first-mail project-folder sync entry plan.
- `pc_background_hardening_plan.md`: near-term hardening order for the repository as a long-running PC-side background process.
- `pc_service_hosting_plan.md`: concrete service-hosting plan centered on Windows Task Scheduler plus `mail_runner.host`.
- `vps_relay_bootstrap_plan.md`: narrow repository-side bootstrap plan for the first VPS relay/control-plane workstream behind the completed outbound layering seam.
- `vps_relay_deploy_runbook.md`: concrete Phase C deployment/runbook for the current lightweight relay skeleton on the inspected Ubuntu VPS.
- `vps_environment_baseline.md`: inspected Ubuntu VPS baseline for the first relay deployment path.
- `coding_backlog.md`: canonical next-phase development backlog for the current repository.
- `codex_sdk_continuous_session_plan.md`: detailed P1 implementation plan for Codex SDK continuous sessions.
- `codex_sdk_capability_probe.md`: capability probe notes for SDK and MCP integration boundaries before P1.
- `p3_streaming_session_window_plan.md`: detailed P3 implementation plan for the first streaming, timestamped, PC-side session window on the `codex + sdk` path.
- `p5_p6_health_and_mail_retention_plan.md`: detailed execution plan for P5 health-state detection and P6 live-mail retention cleanup.
- `p7_acceptance_and_structured_output_plan.md`: detailed execution plan for P7 fixed real-mailbox acceptance and CLI structured output parsing.
- `p8_session_targeting_plan.md`: detailed execution plan for explicit session targeting and routing UX without changing the current Android reply contract.
- `p9_html_mail_projection_plan.md`: narrow P9 plan for Thunderbird/mobile-oriented HTML mail projection while keeping Markdown and plain text as truth layers; repo-side work is partially landed and the remaining plan is temporarily frozen.
- `pc_outbound_layering_refactor_plan.md`: active repository-side implementation plan for splitting the current PC outbound path into render / packet / transport layers without changing the Android-facing contract.
- `pc_outbound_layering_first_slice_checklist.md`: execution-level checklist for the first coding slice of the active PC outbound layering refactor.
- `outbound_mail_baseline_delta_checklist.md`: Phase 0 baseline matrix that records current runtime output versus the frozen outbound contract, and splits the deltas into `P9` versus `post-P9`.
- `android_consumer_contract_alignment_plan.md`: sequencing adjustment plan that prioritizes freezing the Android/Thunderbird-consumable outbound contract before broader outbound convergence work.
- `android_consumer_protocol_freeze_note.md`: short PC-side freeze note listing the outbound contract details that should stay stable while the Android rich-text body slice lands.
- `android_consumer_acceptance_requirements.md`: concrete Android-side success requirements for declaring the current consumer-contract validation complete.
- `android_pc_vps_evolution_authority.md`: current repository-side macro authority for the public-IP plaintext direct-connect direction while keeping current implementation-truth docs unchanged until code changes land.
- `android_pc_vps_coordinated_execution_plan.md`: active cross-repo staged plan for public-IP plaintext direct-connect as the intended Android main path with mail fallback preserved during rollout.
- `android_pc_vps_phase0_phase1_checklist.md`: detailed checklist for Phase 0-1 of the public plaintext direct-connect line: direction reset, baseline freeze, and bootstrap promotion.
- `android_pc_vps_phase0_execution_plan.md`: execution-level plan for reaching the first direct-connect handshake without document drift.
- `phase0_public_plaintext_baseline.md`: exact repository-side mirror of the shared Phase 0 public-IP plaintext baseline, aligned to the Android-side freeze note.
- `phase0_relay_readiness_note.md`: verified readiness note for the chosen public plaintext baseline; records that the live VPS now matches that baseline.
- `phase0_direct_connect_handoff.md`: short repository-side handoff note that closes Phase 0 planning freeze and points Phase 1 at bootstrap promotion with mail fallback preserved.
- `phase1_direct_connect_bootstrap.md`: repository-side Phase 1 bootstrap/seam/failure note that defines the reusable relay bootstrap probe boundary and current fallback taxonomy.
- `phase2_direct_outbound_contract_v1.md`: shared Phase 2 contract freeze for the first direct outbound `new task` slice over the existing relay transport wrapper.
- `phase2_direct_outbound_closeout_handoff.md`: short repository-side handoff note that closes the Phase 2 direct-outbound v1 slice and points the next session at the Phase 3 direct inbound update bridge.
- `phase3_direct_inbound_mapping_v1.md`: shared Phase 3 first-slice mapping note for active-session direct inbound updates into the existing Android session-detail read side.
- `phase3_direct_inbound_wire_v1.md`: shared Phase 3 first-slice wire contract for active-session detail subscribe, `session_update`, ordering, and resync.
- `phase3_direct_inbound_fixture_package_v1.md`: shared Phase 3 representative fixture package note for subscribe identity fallbacks, status snapshots, reconciliation, and resync.
- `phase3_direct_inbound_closeout_handoff.md`: short repository-side handoff note that closes the Phase 3 direct inbound v1 slice and points the next active cross-repo work at Phase 4 dual-stack parity and primary-path switch.
- `phase4_dual_stack_parity_plan.md`: repository-side execution plan for the first Phase 4 covered-flow parity pass, mismatch triage, rollback trigger definition, and the first primary-path switch gate.
- `phase4_dual_stack_parity_checklist.md`: repository-side first validated matrix baseline for the shared Phase 4 parity checklist, currently scoped to `new_task`.
- `phase4_mismatch_ledger.md`: repository-side mismatch-ledger skeleton plus the first validated repo-side readout for Phase 4.
- `phase4_rollback_trigger_note.md`: repository-side first validated trigger baseline for the shared Phase 4 rollback trigger note, currently scoped to `new_task`.
- `phase5_long_term_default_hardening_plan.md`: repository-side pre-freeze execution plan for sequencing the shared Phase 5 documentation set.
- `phase5_long_term_fallback_note.md`: repository-side first draft baseline for the shared Phase 5 long-term fallback note.
- `phase5_token_and_reconnect_handling_note.md`: repository-side first draft baseline for the shared Phase 5 token and reconnect handling note.
- `phase5_remaining_edge_case_ledger.md`: repository-side first draft baseline for the shared Phase 5 remaining edge-case ledger.
- `phase5_freeze_review_precheck.md`: repository-side precheck note for deciding whether the current Phase 5 draft set is ready to enter shared freeze review preparation without swallowing still-open lines.
- `outbound_mail_contract_convergence_plan.md`: broader long-term plan for converging outbound task mail onto a neutral internal model, summary-first plain text, fragment-based HTML projection, and dual-format subject compatibility.

Original outbound sequencing was: freeze the consumer-facing contract first, land the narrow `p9_html_mail_projection_plan.md` reading slice against that frozen contract, and only then start the broader `outbound_mail_contract_convergence_plan.md` work.

As of 2026-03-20, the repo-side P9 slice is partially landed but the remaining plan is temporarily frozen. Use `docs/plans/p9_html_mail_projection_plan.md` as the progress record, and do not treat P9 as the active implementation queue until it is explicitly reopened.

As of 2026-03-20, the repo-side outbound layering refactor is structurally landed in code. `docs/plans/pc_outbound_layering_refactor_plan.md` now serves as the progress record for that completed layering slice and the handoff point for future relay/VPS transport work, while the Android-facing contract remains frozen.

Obsolete mail-first or TLS-gated Android/PC/VPS planning snapshots are intentionally removed from this directory rather
than retained as passive historical context.

The current server-side follow-up for relay work is now split across:

- `docs/plans/vps_relay_bootstrap_plan.md` for repository-scoped implementation sequencing
- `docs/plans/vps_environment_baseline.md` for the inspected VPS baseline
- `docs/platform/relay_transport_protocol_draft.md` for the earlier TLS-backed PC-to-VPS transport draft, now kept mainly as historical or alternative reference rather than as the active Android main-path authority

As of 2026-03-21, Phases A-C of `vps_relay_bootstrap_plan.md` are landed. The repository now has the relay
skeleton, local authenticated loopback, and a live VPS deployment on public `:8787`.

As of the 2026-03-21 plaintext cutover probe, that live deployment now matches the frozen public baseline:
`http://124.223.41.153:8787/healthz` returns `200 OK` with `tls_enabled = false`, and
`ws://124.223.41.153:8787/relay` returns `hello_ack` on the live token path. Use
`docs/plans/phase0_relay_readiness_note.md` as the current repository-side proof package for that baseline.

As of the 2026-03-22 public partial re-probe, `http://124.223.41.153:8787/healthz` still returns `200 OK`, the live
health payload still reports `tls_enabled = false` and now exposes `taskmail_direct_ingress_enabled = true`, and
`ws://124.223.41.153:8787/relay` with an invalid token still returns `unauthorized`. The remaining VPS live-acceptance
gap is therefore no longer basic public reachability, but a fresh valid-token `hello_ack` plus upgraded-path packet /
SMTP delivery verification.

As of 2026-03-21, the remaining repository-side Phase 0 handoff is also explicit in
`docs/plans/phase0_direct_connect_handoff.md`. That means repository-side Phase 0 is now closed, and the next active
Android/PC/VPS slice is Phase 1 bootstrap promotion rather than more baseline debate.

As of 2026-03-21, repository-side Phase 1 bootstrap artifacts are also published in
`docs/plans/phase1_direct_connect_bootstrap.md`. That note closes the repository-side bootstrap probe/seam/failure
taxonomy, while cross-repo Phase 1 still remains open pending Android-side reuse above the debug-only screen.

As of 2026-03-21, the first shared Phase 2 freeze artifact also exists in
`docs/plans/phase2_direct_outbound_contract_v1.md`. That note freezes only the first direct `new task` packet shape,
ack meaning, and fallback matrix; it does not claim that the repository already implements direct Android business
traffic today.

As of 2026-03-21, the repository-side closeout handoff for that first Phase 2 slice is also explicit in
`docs/plans/phase2_direct_outbound_closeout_handoff.md`. That means the next active cross-repo slice should now be
read as Phase 3 direct inbound update bridge rather than more implicit Phase 2 scope growth.

As of 2026-03-21, the first repository-side Phase 3 read-side freeze artifact also exists in
`docs/plans/phase3_direct_inbound_mapping_v1.md`. That note intentionally starts with active-session detail mapping and
mail/direct coexistence rules rather than jumping straight to a full history or workspace-summary API.

As of 2026-03-21, the next companion artifact for that Phase 3 slice is also explicit in
`docs/plans/phase3_direct_inbound_wire_v1.md`. That note freezes the active-session detail subscribe flow, the
`session_update` push message, and the first resync/ordering rules so Android and repository-side work can stop
guessing different wire behavior.

As of 2026-03-21, the first representative fixture companion for that Phase 3 slice is also explicit in
`docs/plans/phase3_direct_inbound_fixture_package_v1.md`. That note freezes the fixture-unit contract, identity
fallback cases, question/status coverage, reconciliation suppress cases, and the first deterministic manifest both
repositories should implement against.

As of 2026-03-22, the repository-side Phase 3 closeout handoff is also explicit in
`docs/plans/phase3_direct_inbound_closeout_handoff.md`. That note reads the current repository slice as a closed first
inbound-update package, records the paired Android-side Phase 3 freeze / Phase 4 start signal, and shifts the next
active cross-repo slice to Phase 4 dual-stack parity and primary-path switch rather than further implicit Phase 3 scope
growth.

As of 2026-03-22, the repository-side Phase 4 execution plan is also explicit in
`docs/plans/phase4_dual_stack_parity_plan.md`. That note keeps the first covered flow conservative (`new_task` first),
defines the planned parity checklist / mismatch ledger / rollback trigger outputs, and keeps direct `reply` /
direct `/status` outside the default implementation queue until a separate cross-repo contract freeze exists.

As of 2026-03-22, the repository-side first evidence baselines for those three shared Phase 4 artifacts also exist in
`docs/plans/phase4_dual_stack_parity_checklist.md`, `docs/plans/phase4_mismatch_ledger.md`, and
`docs/plans/phase4_rollback_trigger_note.md`. Those notes keep the shared artifact names aligned with Android, record
the first repository-side parity / rollback evidence readout, and intentionally keep the mismatch table empty until a
confirmed cross-repo delta is actually evidenced.

As of 2026-03-22, the repository-side `new_task` outcome normalization seam is also landed in code and tests: accepted,
fallback-classified rejection, and hard rejection now share one repo-side classifier, accepted-packet failure can
persist `last_error_code`, Android authority samples now also exist for shared closeout workflow reuse (`thread_097`)
and `request_id`-first bind (`thread_098`), and `hard_rejection_stop` should now be read as a closed shared negative
closeout rather than as a standing tail item for that specific `new_task` row. This does not close other open lines
such as fresh VPS acceptance; it only means that later planning should not keep reopening `hard_rejection_stop` unless
a fresh regression actually appears on the current build.

As of 2026-03-22, the repository-side Phase 5 documentation-sequencing plan is also explicit in
`docs/plans/phase5_long_term_default_hardening_plan.md`. That note keeps the current Phase 5 effort at the
documentation-assembly / pre-freeze stage, defines the fill order for the shared artifacts, and explicitly prevents
Phase 5 from being read as “Phase 4 already closed”.

As of 2026-03-22, the repository-side Phase 5 shared-artifact homes also exist in
`docs/plans/phase5_long_term_fallback_note.md`, `docs/plans/phase5_token_and_reconnect_handling_note.md`, and
`docs/plans/phase5_remaining_edge_case_ledger.md`. All three have now advanced to first draft baselines. None of these
documents imply that the Phase 4 exit gates are already satisfied or that `direct-default` has already switched.

As of 2026-03-22, the repository-side freeze-review-preparation checkpoint is also explicit in
`docs/plans/phase5_freeze_review_precheck.md`. That note does not declare Phase 5 frozen and does not authorize
`direct-default`; it only records that the current draft set now has a reviewable precheck shape while still keeping
open lines such as fresh VPS acceptance visible.

Layering reference: [document_layering_plan.md](../document_layering_plan.md).

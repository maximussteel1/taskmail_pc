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
- `coding_backlog.md`: canonical next-phase development backlog for the current repository.
- `codex_sdk_continuous_session_plan.md`: detailed P1 implementation plan for Codex SDK continuous sessions.
- `codex_sdk_capability_probe.md`: capability probe notes for SDK and MCP integration boundaries before P1.
- `p3_streaming_session_window_plan.md`: detailed P3 implementation plan for the first streaming, timestamped, PC-side session window on the `codex + sdk` path.
- `p5_p6_health_and_mail_retention_plan.md`: detailed execution plan for P5 health-state detection and P6 live-mail retention cleanup.
- `p7_acceptance_and_structured_output_plan.md`: detailed execution plan for P7 fixed real-mailbox acceptance and CLI structured output parsing.

Layering reference: [document_layering_plan.md](../document_layering_plan.md).

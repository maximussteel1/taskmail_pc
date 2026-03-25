# Reference

本目录存放**可复用的操作参考、冒烟手册、环境经验和排障说明**。

这些文档的职责是回答“怎么复跑、怎么验证、有哪些已知环境细节”，而不是回答“系统当前正式语义是什么”。

使用规则：

- 适合放脚本用法、联调步骤、冒烟标准、环境注意事项、已验证结果路径
- 不单独定义当前协议或运行时语义；行为变化时，先更新 `docs/current/`
- 如与 `docs/current/*`、代码或测试冲突，以当前协议和代码事实为准
- 可以引用 `_tmp_*` 下的结果文件作为证据，但不要把单次结果路径写成长期合同

当前文件：

- `sdk_integration_reference.md`
- `vps_only_support_test_targets.md`
- `sdk_runtime_smoke.md`
- `sdk_runtime_smoke_validation.md`
- `sdk_question_answer_smoke.md`
- `sdk_question_answer_smoke_validation.md`
- `sdk_permission_smoke.md`
- `sdk_permission_smoke_validation.md`
- `sdk_stream_smoke.md`
- `sdk_stream_smoke_validation.md`
- `waiting_state_variant_smoke.md`
- `waiting_state_variant_smoke_validation.md`
- `artifact_contract_smoke.md`
- `artifact_contract_smoke_validation.md`
- `pc_control_plane_fixture_smoke.md`
- `pc_control_plane_fixture_smoke_validation.md`
- `opencode_sdk_smoke.md`
- `opencode_sdk_validation.md`

分层依据见 [document_layering_plan.md](../document_layering_plan.md)。

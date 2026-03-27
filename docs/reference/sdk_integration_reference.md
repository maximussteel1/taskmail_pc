# SDK 接入参考基线

## 目的

本文作为 `vps-first` 重构期间的 SDK 接入参考基线，服务于后续同时支持 `OpenCode SDK` 和 `Codex SDK`，并以当前 `sdk-first` runtime 接线为主线。

它回答的不是“当前协议是什么”，而是：

- 在真正把某个 SDK 接进 runtime 之前，应该先把哪些使用方式搞清楚
- 每个 SDK 需要留下哪些可复跑入口和验证证据
- 参考文档与验证结果文档应该怎么分工

当前 `vps-only` 开发支持测试目标见 [vps_only_support_test_targets.md](./vps_only_support_test_targets.md)，其中 SDK 测试只是执行接线层的一部分。

## 为什么单独建这层

对后续重构来说，SDK 相关信息至少有三类：

1. 当前正式行为
2. 如何接入、如何验证、有哪些环境限制
3. 某次具体验证的结果与证据

其中第 1 类属于 `docs/current/`，但第 2、3 类不应该混进协议层，否则会把“临时环境经验”误写成“长期语义合同”。

因此当前口径是：

- `docs/current/`：正式语义、当前行为边界
- `docs/reference/*_smoke.md` / `*_reference.md`：使用方式、复跑路径、排障经验
- `docs/reference/*_validation.md`：某条链路当前已经测到什么、证据在哪、还缺什么

## 每个 SDK 在接入前应先澄清的最小问题

无论是 `OpenCode` 还是 `Codex`，至少先确认以下几点：

1. 认证来源是什么。
2. SDK 直连的是本地进程、本地 HTTP 服务，还是远端 API。
3. session 的创建、续接、消息发送、结果读取分别怎么做。
4. provider / model 的默认选择逻辑是什么。
5. workspace / 文件写入 / 权限语义怎么投影。
6. streaming、取消、超时和清理分别怎么处理。
7. 在 Windows 和未来 VPS 场景下，有没有额外的进程或网络注意事项。
8. 有没有最小可复跑脚本、自动校验、测试入口和结果落盘。

如果这八项里还有关键空白，不应直接把该 SDK 当成 runtime 主路径。

## 推荐落地顺序

对每个 SDK，建议按以下顺序推进：

1. 先做独立的 runtime smoke，验证最简单的真实任务。
2. 再做独立的 waiting-state / question-answer smoke，确认 `awaiting_user_input -> answer -> resume`。
3. 对 waiting-state 的多题、paused、partial-answer 变体，再补一条独立 fixture smoke。
4. 如有必要，再做更低层的 SDK 直连 smoke。
5. 把调用入口、限制条件和已知坑写成参考文档。
6. 把实际命令、结果、证据路径和未覆盖项写成验证结果文档。

这样做的目的，是先把 `sdk-first` runtime 主路径立住，再把更底层的 SDK 自身问题与 runtime 集成问题拆开定位。

## 当前覆盖状态

- OpenCode：已有低层 Python SDK smoke、runtime `sdk-first` smoke，以及 runtime question-answer smoke。
- Codex：已有 runtime `sdk-first` smoke，以及 runtime question-answer smoke。
- 两个 backend 现在都已有独立的 runtime permission smoke。
- `Codex` 当前已有独立 stream smoke；`OpenCode` 当前也已有独立 stream smoke，并已在 `2026-03-27` 用真实 `sdk_stream_smoke` 留下 same-layer incremental message-part evidence。当前 smoke 仍保留 residual-gap 记录能力，但只在未来回退到非 incremental `stream_mode` 时触发。
- 当前真实 SDK smoke 默认走独立脚本，不挂在 `tests/` 主测试集。
- 当前真实 SDK smoke 的验证结果都要求包含收尾清理证据，例如端口关闭或 sidecar 记录清除。

## 当前文档清单

- [vps_only_support_test_targets.md](./vps_only_support_test_targets.md)
  冻结当前 `vps-only` 开发线需要先补齐的支持测试，其中 SDK 测试只是执行接线层的一部分。
- [sdk_runtime_smoke.md](./sdk_runtime_smoke.md)
  说明当前 `sdk-first` runtime smoke 怎么复跑。
- [sdk_runtime_smoke_validation.md](./sdk_runtime_smoke_validation.md)
  记录当前 `OpenCode` / `Codex` runtime smoke 的真实结果与证据。
- [sdk_question_answer_smoke.md](./sdk_question_answer_smoke.md)
  说明当前 `sdk-first` waiting-state / question-answer smoke 怎么复跑。
- [sdk_question_answer_smoke_validation.md](./sdk_question_answer_smoke_validation.md)
  记录当前 `OpenCode` / `Codex` question-answer smoke 的真实结果、差异和证据。
- [waiting_state_variant_smoke.md](./waiting_state_variant_smoke.md)
  说明多题 waiting-state / pause-resume 变体 smoke 怎么复跑。
- [waiting_state_variant_smoke_validation.md](./waiting_state_variant_smoke_validation.md)
  记录多题 waiting-state / pause-resume 变体 smoke 的结果和证据。
- [sdk_permission_smoke.md](./sdk_permission_smoke.md)
  说明当前 `sdk-first` permission smoke 怎么复跑。
- [sdk_permission_smoke_validation.md](./sdk_permission_smoke_validation.md)
  记录当前 `OpenCode` / `Codex` permission smoke 的真实结果、投影差异和证据。
- [sdk_stream_smoke.md](./sdk_stream_smoke.md)
  说明当前 `sdk-first` stream smoke 怎么复跑。
- [sdk_stream_smoke_validation.md](./sdk_stream_smoke_validation.md)
  记录当前 `Codex` persisted stream evidence 与 `OpenCode` same-layer incremental stream evidence 的结果和证据；若 future run 退回非 incremental `stream_mode`，也会在这里继续显式记录 residual gap。
- [opencode_sdk_smoke.md](./opencode_sdk_smoke.md)
  说明 OpenCode Python SDK 的低层直连 smoke。
- [opencode_sdk_validation.md](./opencode_sdk_validation.md)
  记录当前 OpenCode 低层 SDK smoke 已完成的测试、结果和证据路径。

后续新增 `Codex SDK` 参考时，沿用同样分法：

- `codex_sdk_*.md`：怎么用
- `codex_sdk_*validation*.md`：测到了什么

## 对后续 vps-first 重构的意义

这层文档的价值不在于定义协议，而在于减少未来接入成本：

- 不需要反复重新确认 SDK 的最小调用方式
- 不需要每次都从零排查 provider / model / auth / cleanup 问题
- 能把“SDK 自身问题”和“runtime 集成问题”分开定位
- 后续补 `Codex SDK` 时可以直接复用同一套结构

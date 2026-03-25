# OpenCode Python SDK 使用参考

## 文档关系

- SDK 总体接入基线见 [sdk_integration_reference.md](./sdk_integration_reference.md)
- 当前 runtime 主线 smoke 见 [sdk_runtime_smoke.md](./sdk_runtime_smoke.md)
- 本文聚焦 OpenCode Python SDK 的最小可复跑用法
- 当前验证结果与证据见 [opencode_sdk_validation.md](./opencode_sdk_validation.md)

## 定位

本文记录本仓库里 OpenCode Python SDK 的最小可复跑路径，以及 Windows 环境下需要注意的清理细节。

它放在 `docs/reference/`，不是 `docs/current/`，因为这里描述的是：

- 怎么验证本机全局 OpenCode 登录态能否被本地 SDK 链路实际使用
- 怎样最小成本地打一轮真实对话
- 这次联调里暴露出的环境经验和清理口径

这些内容有复用价值，但不属于当前 mail protocol 或运行时协议真相层。

## 相关入口

- 入口脚本：`.\.venv\Scripts\python.exe .\scripts\opencode_sdk_smoke.py`
- 实际实现：`mail_runner/opencode_sdk_smoke.py`
- 依赖包：`opencode-ai>=0.1.0a36`

## 已验证范围

`2026-03-25` 已完成一轮真实最小任务验证，目标是让模型只在临时 workspace 中创建一个文本文件，不发送复杂任务。

详细结果和证据路径单独记录在 [opencode_sdk_validation.md](./opencode_sdk_validation.md)。这里仅保留当前已确认的最小结论：

- 本机全局 `opencode` 登录态可通过 `opencode serve` 被 SDK 链路实际使用
- Python SDK 默认通过 HTTP 连接本地 `opencode serve`
- 当前仓库已经有可复跑的最小脚本和结果落盘入口
- 这条低层 smoke 当前不挂在 `tests/` 主测试集里

## 为什么这条链路能验证“全局配置生效”

这次验证确认了两件事：

1. `opencode` CLI 的全局登录态可被本机 `opencode serve` 读取。
2. Python SDK 默认不是直接去读 `~/.local/share/opencode/auth.json`，而是通过 HTTP 连本地 `opencode serve`。

因此，仓库里的正确复跑方式不是直接假设 `Opencode()` 会自动继承全局配置，而是：

1. 临时拉起一个本地 `opencode serve`
2. 让 Python SDK 连这个本地服务
3. 用最小任务验证 provider/model 选择和实际对话都正常

## 冒烟脚本做了什么

脚本会按以下顺序执行：

1. 选一个本地空闲端口，启动临时 `opencode serve`
2. 轮询 `client.config.get()`，等待服务 ready
3. 读取 `client.app.providers()`，优先选择 provider 默认模型；必要时也支持 `--provider-id` / `--model-id`
4. 创建一个临时 session，发出最小 prompt
5. 验证 `workspace/smoke_note.txt` 是否存在且内容完全匹配
6. 验证 assistant reply 是否包含 `STATUS: OK` 和 `FILE: smoke_note.txt`
7. 将结果写入 `result.json`
8. 默认自动清理临时 `opencode` 进程

默认 prompt 很刻意地保持简单，只要求：

- 创建 `smoke_note.txt`
- 写入一行固定文本
- 不修改其他文件
- 最终只回复两行固定文本

这样可以把验证目标收敛到“服务拉起成功、SDK 对话成功、模型有真实文件写权限、结果可校验”。

## 复跑命令

最常用的复跑方式：

```powershell
.\.venv\Scripts\python.exe .\scripts\opencode_sdk_smoke.py
```

如果想固定 run 目录名，便于留存结果：

```powershell
.\.venv\Scripts\python.exe .\scripts\opencode_sdk_smoke.py --run-name opencode-sdk-smoke-cleanup-check
```

常用可选参数：

- `--provider-id <id>`：显式指定 provider
- `--model-id <id>`：显式指定 model
- `--workspace <path>`：指定临时 workspace
- `--port <port>`：指定本地 `opencode serve` 端口
- `--leave-server-running`：保留临时服务，便于手动调试

## Windows 清理经验

这次联调里真正踩到的问题不是“对话失败”，而是 Windows 下临时 `opencode serve` 退出链不干净，容易残留监听进程。

当前脚本已经固定了两层清理：

- 先对启动的 wrapper pid 执行 `taskkill /T /F`
- 再按监听端口反查 `OwningProcess`，补杀仍占用该端口的残留进程

因此，当前口径是：

- 冒烟脚本默认负责清理
- 如果需要人工排障，可带 `--leave-server-running`
- 一旦怀疑有残留，优先看 `serve.stdout.log`、`serve.stderr.log` 和结果目录下的 `result.json`

## 什么时候该更新这篇文档

出现以下任一变化时，应同步更新本文：

- `scripts/opencode_sdk_smoke.py` 或 `mail_runner/opencode_sdk_smoke.py` 的参数、默认行为或验证标准发生变化
- OpenCode Python SDK 的连接方式变化，导致“不再需要本地 `opencode serve`”或 provider/model 选择逻辑变化
- Windows 清理口径再次调整
- 仓库决定把这条链路升级为更正式的长期诊断入口

如果未来某些行为已经上升为当前运行时正式要求，应把语义规则写入 `docs/current/`，本文只保留复跑与经验部分。

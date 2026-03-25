# OpenCode Python SDK 验证结果

## 目的

本文记录当前仓库对 OpenCode Python SDK 已完成的实际验证、结果摘要、证据路径和未覆盖项。

它与 [opencode_sdk_smoke.md](./opencode_sdk_smoke.md) 的分工是：

- `opencode_sdk_smoke.md` 讲“怎么复跑”
- 本文讲“目前已经实际测到了什么”

## 本次记录对应的环境

- 日期：`2026-03-25`
- 机器：当前本地 Windows 开发机
- 仓库：`E:\projects\mail_based_task_manager`
- Python：`.\.venv\Scripts\python.exe`
- SDK 包：`opencode-ai>=0.1.0a36`

## 已完成验证

### 1. 安装与导入

已确认 Python 包安装在仓库虚拟环境内，并可成功导入：

- 包名：`opencode-ai`
- 导入入口：`from opencode_ai import Opencode`

### 2. 独立 smoke 入口

当前这条低层 OpenCode SDK 验证不挂在 `tests/` 主测试集里。

当前口径是：

- 保留独立脚本
- 保留结果落盘
- 不让 full suite 每次都触发真实 SDK smoke

### 3. 真实最小任务冒烟

已运行：


```powershell
.\.venv\Scripts\python.exe .\scripts\opencode_sdk_smoke.py --run-name opencode-sdk-smoke-post-refactor-check
```

验证目标：

- 临时拉起 `opencode serve`
- 通过 Python SDK 发一个最小任务
- 只创建一个文本文件，不发送复杂任务
- 自动校验模型回复和产物文件

结果：

- 状态：成功
- provider：`alibaba-coding-plan-cn`
- model：`qwen3.5-plus`
- assistant reply：`STATUS: OK` / `FILE: smoke_note.txt`
- 产物文件内容：`hello from opencode sdk smoke`

## 关键证据

最新成功结果文件：

- `_tmp_opencode_sdk_smoke/opencode-sdk-smoke-post-refactor-check/result.json`

对应产物文件：

- `_tmp_opencode_sdk_smoke/opencode-sdk-smoke-post-refactor-check/workspace/smoke_note.txt`

结果文件中可直接看到：

- `success: true`
- `provider_id: "alibaba-coding-plan-cn"`
- `model_id: "qwen3.5-plus"`
- `assistant_reply: "STATUS: OK\nFILE: smoke_note.txt"`
- `file_content: "hello from opencode sdk smoke"`

## 当前已确认结论

1. 本机全局 `opencode` 登录态可以通过本地 `opencode serve` 被实际使用。
2. Python SDK 默认不是直接读取全局 `auth.json`，而是通过 HTTP 连接本地 `opencode serve`。
3. 当前仓库已经具备可复跑的最小验证入口：脚本、自动校验、结果落盘都已齐备。
4. Windows 下清理临时 `opencode serve` 时，单杀 wrapper pid 不够；还需要按监听端口补杀残留 listener。
5. 本次把公共 OpenCode SDK 逻辑抽出后，低层直连 smoke 已再次复跑通过，说明现有 `opencode_sdk_smoke.py` 入口未被这轮 `sdk-first` runtime 改造带坏。

## 当前未覆盖项

以下内容还没有纳入这次验证：

- streaming 事件消费
- 多轮 session 续接
- 显式 cancel / kill
- 长时间运行与超时恢复
- VPS 场景下的 SDK 拓扑
- `Codex SDK` 的同层参考与验证对照

因此，当前结论只适用于：

- 本地 Windows 环境
- 最小真实任务
- Python SDK 通过本地 `opencode serve` 的使用路径

## 后续更新条件

出现以下情况时，应更新本文：

- 冒烟脚本行为或参数发生变化
- 再次复跑并生成新的代表性结果
- 补上 streaming / resume / cancel / VPS 相关验证
- 开始做 `Codex SDK` 对照验证，需要对齐口径

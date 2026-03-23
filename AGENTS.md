# AGENTS

## 目标

- 本文件面向编码代理，不面向普通读者。
- 只依据当前仓库事实工作，不依据未来平台设想工作。
- 这是个人单用户仓库。默认优化目标是长期可维护性、结构清晰度和复杂度下降，不是广泛的内部向后兼容。

## 默认行动模式

- 默认允许为了降低复杂度而进行中到大规模重构。
- 默认允许移动文件、重命名符号、合并或拆分模块、删除死代码、替换明显不合理的内部结构。
- 默认不要因为“改动面大”或“可能影响内部兼容”而放弃更好的内部方案。
- 只有触发“稳定边界”或“高风险判定”时，才切换为保守模式。

## 决策顺序

1. 用户当前任务中的明确要求。
2. `docs/current/` 中已声明的当前协议与运行时行为。
3. 本文件定义的稳定边界。
4. 长期可维护性、代码清晰度、结构一致性、复杂度下降。
5. 局部最小改动与内部兼容性。

如果第 4 项与第 5 项冲突，优先第 4 项。

## 环境

- 仓库根目录：`E:\projects\mail_based_task_manager`
- Shell：Windows PowerShell
- 首选 Python：`.venv\Scripts\python.exe`
- 不要依赖裸 `python`；在这台机器上它可能解析到 Windows Store stub。

## 常用命令

- 完整测试：`.venv\Scripts\python.exe -m pytest`
- 单模块测试：`.venv\Scripts\python.exe -m pytest tests/test_reporter.py`
- 单次运行 mail runner：`.venv\Scripts\python.exe -m mail_runner.app --once --config .\mail_config.local.yaml`
- 本地循环运行：`.venv\Scripts\python.exe -m mail_runner.app --loop --config .\mail_config.local.yaml`

## 仓库结构

- `mail_runner/`：运行时代码
- `tests/`：自动化测试；行为变化时同步扩展或更新
- `docs/current/`：当前协议与运行时行为的事实来源
- `docs/plans/`：实现计划与演进方向
- `docs/platform/`：未来平台相关文档，不代表当前行为
- `tasks/`：运行时状态与产物；不要当作已提交源码
- `_tmp_*/`：本地验证输出；有用可保留，否则忽略

## 文档规则

- 读取仓库文档时显式使用 UTF-8 编码。
- 尤其对 `AGENTS.md`、`README.md`、`state.md` 与 `docs/` 下文件，不要依赖 PowerShell 或终端默认编码。
- 如果 `README.md`、`state.md` 与 `docs/current/` 冲突，以 `docs/current/` 为准。
- 当前行为变化时，在同一改动中优先更新 `docs/current/`。
- 实现方向变化但当前行为未变化时，更新 `docs/plans/`。
- 新增或更新仓库文档时，默认使用中文；除非任务明确要求其他语言。
- 保持文档分层：当前事实放 `docs/current/`，计划放 `docs/plans/`，未来平台内容放 `docs/platform/`。

## 稳定边界

除非任务明确要求改变语义，否则保留以下边界：

- mail protocol 的语义
- state/question capsule 行为
- reply-routing 行为
- 持久化状态格式
- `RunArtifact` 与 `artifact_index.json` 作为产物真相层的角色
- reporter 输出中只有一个 `Artifacts` section
- `Attachment Notices` 仍位于 `Artifacts` 之后并保持独立
- 外发邮件仍投影为 `text/plain` + `text/html`
- 不要把 `cid:` 等 mail-specific 字段写入 `artifact_index.json`

## 高风险判定

出现以下情况时，先读取相关测试与 `docs/current/`，再实施修改：

- 无法判断某项改动属于内部实现还是稳定边界
- 改动会改变协议语义、状态格式、产物索引结构或服务维护路径
- 改动需要处理迁移、兼容或历史数据读写
- 改动会影响 scheduler、reply-routing、mail protocol 的关键控制流

## 内部实现策略

- 内部实现允许主动重构。
- 不要为了保留既有内部结构而保留明显不合理的设计。
- 大改动本身不是风险信号；是否推进，取决于是否触及稳定边界与是否改善整体结构。
- 当较大改动能显著降低复杂度、提升可维护性、统一模型或减少重复时，可以直接推进。
- 如果重构会触及稳定边界，必须同时更新测试与文档，并明确处理迁移或兼容问题。
- scheduler、reply-routing、mail protocol 相关代码可以重构，但先读相关测试与 `docs/current/`，不要盲改。
- 本地配置文件如 `config.yaml`、`mail_config.local.yaml` 默认不要提交，除非任务明确要求修改受跟踪示例。

## 测试与验证

- 修改 rendering、mail IO、parsing、state persistence 时，在同一改动中新增或更新测试。
- 代码改动先跑目标测试；如果影响共享运行路径，再跑完整测试。
- 纯文档改动通常不要求跑测试；如果文档改动了示例命令或路径，应补充验证。

## 服务维护

- 本地 Windows 服务维护优先使用 `.\mail_config.bot.relay.local.yaml`，运行目录使用 `.\_tmp_live_mail_runner`。
- 启停与状态检查统一使用 `scripts\manage_mail_runner.ps1`，不要自行拼装临时启动命令。
- runner 存活判断以 `.\_tmp_live_mail_runner\host_state.json` 为第一真相源。
- `.\_tmp_live_mail_runner\loop.pid` 只是辅助线索，不比 `host_state.json` 更可信。
- 如果维护输出互相矛盾，先检查 `host_state.json` 中记录的 PID，再读取最近的 `.\_tmp_live_mail_runner\loop.stderr.log`，再判断 runner 是否真的停止。
- 在 agent 或其他非交互 shell 中，`start` / `restart` 超时不等于启动失败。超时后先检查 `host_state.json`，再运行 `scripts\manage_mail_runner.ps1 status`，再决定是否再次重启。
- 当前脚本优先依据运行时元数据，不依赖 CIM/WMI 进程扫描，并通过外部 detached PowerShell launcher 让服务在非交互 shell 中也能启动。
- 对 relay-enabled 配置，`scripts\manage_mail_runner.ps1` 还会管理 `sync_relay_task_root.py --repeat-seconds 2` companion，默认使用仓库根目录 `work_bot.pem`，把本地 authoritative `task_root` 持续同步到 VPS relay 可见的 `/opt/mail_runner_relay/shared/task_root`。
- 如果 `status` 显示 host 在跑但 `Relay task-root sync` companion 缺失，先修复 companion 或 task-root 可见性，不要先怀疑 Android direct lane；否则 `current-session` direct `reply` / `/status` 可能继续报 locator resolution failure。
- 在这台机器上，不要把 detached launcher 切回 `Register-ScheduledTask`。当前维护路径是隐藏式 `Start-Process powershell.exe ...` 加 `host_state.json` 校验；`Register-ScheduledTask` 在 agent shell 或提升权限 shell 中可能会在 host 真正启动前卡住。

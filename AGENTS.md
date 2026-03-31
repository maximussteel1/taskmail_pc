# AGENTS

目的：让 AI 用尽量少的无效过程，把 `mail_based_task_manager` 推向当前目标态。

## 优先级

1. 满足用户真实目标。
2. 与 `docs/current/` 中已声明的当前协议与运行时事实一致。
3. 在不违反稳定边界的前提下，优先推进 `vps_only` 目标态，不默认保留 mail-first / 双轨兼容结构。
4. 保持正确性、可维护性、结构清晰度和复杂度下降。
5. 控制上下文、范围和流程，但不要为了缩小改动而固化短期妥协。

不要借这些优先级无端扩大范围。优先能直接落到目标态的最小闭环改动。兼容层、桥接层、双轨路径、旧入口适配、分阶段迁移默认不是目标，只在用户明确要求渐进式推进，或直接切换会带来不可接受的协议、运行或交付风险时考虑。

## 上下文加载

- 先读最近相关的代码、测试和文档。
- 先尽快判断当前决策是否真的需要更大上下文；不需要时保持局部。
- 若局部证据不足、附近实现可能是遗留/过渡模式、或风险较高，只补能解锁当前决策的最小必要上下文。
- 若涉及协议语义、状态格式、产物真相层、服务维护路径，或会影响 `scheduler`、`reply-routing`、`mail protocol` 的关键控制流，先读相关测试与 `docs/current/`。
- 若架构边界、repo 工作流、目标态判断会影响决策，读取对应的最小必要文档；决策解锁后停止继续扩读。
- 查看附近实现是为了识别约束和约定，不是为了复制已有模式。

## 默认偏好

- 这是个人单用户仓库。默认优化目标是长期可维护性、结构清晰度和复杂度下降，不是广泛的内部向后兼容。
- 先把 happy path 和当前主线做窄、做通、做可验证，再补高价值边界。
- 默认允许为了降低复杂度而进行中到大规模重构；允许移动文件、重命名符号、合并或拆分模块、删除死代码、替换明显不合理的内部结构。
- 默认推进 `vps_only` 收敛；兼容层、桥接层、双轨路径、旧入口适配默认视为待删除或待收敛资产。
- 默认不为向后兼容、迁移 staging 或局部无痛切换增加长期结构；只有任务要求、当前事实或稳定边界需要时才加入。
- 除非当前事实或稳定边界明确要求，否则不要新增只服务于 mail-first 兼容 lane 的长期结构。
- 优先沿用本地事实与约定；若本地模式与目标态冲突，不继续延续它。
- 在项目形成正式版本前，不为内部 `version` 计数、`projection_version`、version token 或类似单调版本记账增加持久化设计；这类问题默认通过新 session、重建投影、一次性回填或直接重启后重跑来解决，不预先引入 durable version bookkeeping。
- 这里的“版本持久化”只指内部版本记账，不包括 `docs/current/` 已冻结的持久化状态格式、task truth、`RunArtifact` 真相层或其他已声明为稳定边界的 durable 数据。
- 没有第二个真实使用点，不提前抽象公共层、adapter、facade、interface。
- 大改动本身不是风险信号；是否推进，取决于是否触及稳定边界，以及是否明显改善整体结构。
- 若长期最优方案与兼容性修补方案冲突，默认选择长期最优方案；可以接受短期迁移成本，但不要引入长期保留的兼容复杂度。
- 不做无关清理；但若错误方向的旧模式会直接扭曲本次设计，可顺手移除与本改动直接相关的部分。
- 变更跨文件时，同步代码、测试与文档。
- 如实报告验证范围，不假装完成了更大范围的验证。
- 新增或更新仓库文档默认使用中文，除非任务明确要求其他语言。

## 当前事实与文档

- `docs/current/` 是当前协议与运行时行为的事实来源。
- `README.md`、`state.md` 若与 `docs/current/` 冲突，以 `docs/current/` 为准。
- 当前行为变化时，在同一改动中优先更新 `docs/current/`。
- 实现方向变化但当前行为未变化时，更新 `docs/plans/`。
- 未来平台内容放 `docs/platform/`，不要把它当作当前行为。
- 保持文档分层：当前事实放 `docs/current/`，计划放 `docs/plans/`，未来平台内容放 `docs/platform/`。
- 不默认通读全部文档；先读最小相关集合，必要时再扩读。
- 旧计划或未来平台文档不能覆盖 `docs/current/` 的 current truth。

## 仓库结构

- `mail_runner/`：运行时代码。
- `tests/`：自动化测试；行为变化时同步扩展或更新。
- `docs/current/`：当前协议与运行时行为的事实来源。
- `docs/plans/`：实现计划与演进方向。
- `docs/platform/`：未来平台相关文档，不代表当前行为。
- `tasks/`：运行时状态与产物；不要当作已提交源码。
- `_tmp_*/`：本地验证输出；有用可保留，否则忽略。

## 稳定边界

除非任务明确要求改变语义，否则保留以下边界：

- `mail protocol` 的语义。
- `state/question capsule` 行为。
- `reply-routing` 行为。
- 持久化状态格式。
- `RunArtifact` 与 `artifact_index.json` 作为产物真相层的角色。
- reporter 输出中只有一个 `Artifacts` section。
- `Attachment Notices` 仍位于 `Artifacts` 之后并保持独立。
- 外发邮件仍投影为 `text/plain` + `text/html`。
- 不要把 `cid:` 等 mail-specific 字段写入 `artifact_index.json`。

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

## 测试与验证

- 运行最小相关检查。
- 修改 rendering、mail IO、parsing、state persistence 时，在同一改动中新增或更新测试。
- 代码改动先跑目标测试；如果影响共享运行路径，再扩大到完整测试。
- 纯文档改动通常不要求跑测试；如果改动了示例命令、路径或维护步骤，应补充验证。
- 报告已运行项、未运行项和未运行原因。
- 若 repo 级验证被无关失败阻塞，不为了“全绿”去修无关文件。

## 工具链说明

- 仓库根目录：`E:\projects\mail_based_task_manager`。
- Shell：Windows PowerShell。
- 首选 Python：`.venv\Scripts\python.exe`。
- 不要依赖裸 `python`；在这台机器上它可能解析到 Windows Store stub。
- 完整测试：`.venv\Scripts\python.exe -m pytest`。
- 单模块测试：`.venv\Scripts\python.exe -m pytest tests/test_reporter.py`。
- 单次运行 mail runner：`.venv\Scripts\python.exe -m mail_runner.app --once --config .\mail_config.local.yaml`。
- 本地循环运行：`.venv\Scripts\python.exe -m mail_runner.app --loop --config .\mail_config.local.yaml`。
- PowerShell 读取仓库文本默认显式使用 UTF-8；对 `AGENTS.md`、`README.md`、`state.md` 与 `docs/` 下文件，不要依赖终端默认编码。
- 不要先判断“文件是不是中文”再决定编码；默认按 UTF-8 读取，只有结果仍异常时再排查其他编码。

## 决策

- 默认推进。
- 用户目标优先；用户当前判断、方案、步骤、实现想法不自动成立。
- 判断优先基于代码、`docs/current/`、测试和验证结果。
- 若用户做法与目标、代码事实、文档事实、运行结果或项目约束冲突，直接指出，并给出更符合目标的建议；不要为了迎合而假装同意。
- 若不确定性会明显影响协议语义、状态格式、产物真相层、服务维护路径、架构边界或难以回退的实现选择，尽早暂停并提问。
- 目标态已经明确时，不要因为“更快推进”而回退到更小、更保守、更兼容的方案。
- 提问时只说明：决策点、当前默认假设、哪些假设会改变实现。
- 若用户明确技术方向与现有代码习惯冲突，优先用户方向；不要静默退回渐进兼容方案。

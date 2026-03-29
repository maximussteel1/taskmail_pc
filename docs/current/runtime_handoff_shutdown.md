# Runtime Handoff Shutdown

## 目的

这份文档记录“把工作电脑上的 mail runner 停掉，再切到另一台电脑接管”的当前安全流程。

这不是普通的 `stop` 备忘录，而是一个带前置检查的 handoff 流程。原因是：

- `scripts/manage_mail_runner.ps1 shutdown` 会强制杀进程，不会优雅排空
- `accepted/running` 任务不能靠 `/pause` 或 `/end` 安全停住底层 CLI
- Windows 聚焦 active-session window 在 `thread` 仍为 `active` 时被手动关掉，可能触发 controller 的本地 close request
- `manage_mail_runner.ps1` 和 `cleanup_project_codex.ps1` 如果喂错 config/runtime/task_root，可能停错实例或漏掉 sidecar 残留

## 当前推荐命令

优先使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\safe_shutdown_mail_runner.ps1 `
  -ConfigPath .\_tmp_live_mail_runner\mail_config.loop_30s.yaml `
  -RuntimeDir .\_tmp_live_mail_runner
```

等价 `.cmd` 入口：

```cmd
.\scripts\safe_shutdown_mail_runner.cmd -ConfigPath .\_tmp_live_mail_runner\mail_config.loop_30s.yaml -RuntimeDir .\_tmp_live_mail_runner
```

如果希望同时清掉当前 task root 下残留的 tracked Codex sidecars，可再加：

```powershell
-StopTrackedSidecars
```

## 脚本做的事

`scripts/safe_shutdown_mail_runner.ps1` 当前会按这个顺序执行：

1. 强制要求显式传入 `ConfigPath` 和 `RuntimeDir`
2. 从这份 config 中解析 `task_root`，并按 config 所在目录解析成真实 task root
3. 先跑一次 `manage_mail_runner.ps1 status`
4. 扫描该 task root 下所有 `thread_state.json`
5. 只要发现任何 `status=accepted` 或 `status=running`，立刻拒绝停服并列出阻塞线程
6. 若没有阻塞线程，再调用 `manage_mail_runner.ps1 shutdown`
7. 再跑一次 `manage_mail_runner.ps1 status`，确认结果是 `Mail runner is not running.`
8. 最后用同一个 task root 跑 `cleanup_project_codex.ps1 status`

## 为什么不用只看 host_state

`host_state.json` 只能告诉你 host 进程有没有活着，不能替代“当前有没有还在跑的任务”这件事。

真正的 handoff 阻塞条件仍然是：

- task root 下是否还有 `accepted`
- task root 下是否还有 `running`

只要这两种状态还存在，就不该直接执行强制 shutdown。

## active-session window 注意事项

停服前不要先手动关聚焦 active-session window。当前实现里：

- 如果 thread 仍为 `active`
- 聚焦 active-session window 被 `Ctrl+C`、右上角 `X` 或 terminal pane 关闭

controller 可能会补发本地 close request，必要时先结束当前 run，再把 session 标成 `ended`。

因此 handoff 流程应先看状态，再决定是否 `/kill` 或等待完成，而不是先关聚焦 active-session window。

## 切机后的动作

如果新电脑需要续旧会话，停完后先同步这份 task root，再在新机器启动 host。

否则新机器只能作为“新实例接管邮箱”启动，未必能续上之前仍想保留的线程上下文。
